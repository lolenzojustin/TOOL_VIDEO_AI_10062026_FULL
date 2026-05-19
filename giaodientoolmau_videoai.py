import sys
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QMessageBox

import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import os
import time
import threading
import requests


# Import các file phụ trợ
from tool_video_ai_layout_3_UI import Ui_Widget
from GpmGlobalApi_tuviet import Gpm

class MultiThread(QThread):
    # Khai báo signal trả về: (Số thứ tự cảnh, trạng thái, kết quả)
    record = pyqtSignal(int, str, str)

    def __init__(self, index, api_url_gpm, profile_id="", prompt_text="test cảnh", win_size="800,800", win_pos="0,0"):
        super().__init__()
        self.index = index
        self.api_url_gpm = api_url_gpm  # URL GPM lấy từ giao diện
        self.profile_id = profile_id    # ID Profile cho luồng này
        self.prompt_text = prompt_text
        self.win_size = win_size
        self.win_pos = win_pos
        self.is_running = True
        self._stop_event = threading.Event()  # Event để dừng sleep ngay lập tức

    def run(self):
        gpm = Gpm()
        profile_id = self.profile_id
        
        status = None
        response_data = "-"
        try:
            # 1. Kiểm tra ID Profile GPM
            if not self.is_running:
                status = "Đã dừng"
                return
            if not profile_id:
                raise RuntimeError("Chưa có ID Profile cho cảnh này.")
            
            # 2. Mở Profile GPM
            if not self.is_running:
                status = "Đã dừng"
                return
            self.record.emit(self.index, "Đang mở GPM", "-")
            remote_addr = gpm.open_profile(apiurl_Gpm=self.api_url_gpm, id_profile=profile_id, win_pos=self.win_pos, win_size=self.win_size)
            
            # Kiểm tra kết quả open_profile
            if not remote_addr:
                raise RuntimeError("open_profile trả về None — GPM chưa khởi động hoặc API URL GPM sai.")
            
            # 3. Kéo Playwright vào điều khiển trình duyệt
            if not self.is_running:
                status = "Đã dừng"
                return
            self.record.emit(self.index, "Đang chạy Playwright", "-")
            with sync_playwright() as p:
                browser = None
                for attempt in range(5):
                    try:
                        browser = p.chromium.connect_over_cdp(f"http://{remote_addr}")
                        break
                    except Exception as e:
                        print(f"[Cảnh {self.index}] Lỗi kết nối CDP (lần {attempt+1}), thử lại sau 2s...")
                        time.sleep(2)
                if not browser:
                    raise RuntimeError("Không thể kết nối Playwright với trình duyệt.")
                
                context = browser.contexts[0]
                # Lấy tab mặc định hoặc tạo tab mới
                page = context.pages[0] if context.pages else context.new_page()
                
                # Áp dụng cứng viewport size cho Playwright để hiển thị chính xác
                try:
                    w, h = map(int, self.win_size.split(","))
                    page.set_viewport_size({"width": w, "height": h})
                except Exception:
                    pass
                
                # Bước 1: vào trang https://labs.google/fx/vi/tools/flow
                try:
                    page.goto("https://labs.google/fx/vi/tools/flow", timeout=60000, wait_until="domcontentloaded")
                except PlaywrightTimeoutError:
                    print(f"[Cảnh {self.index}] goto timeout, tiếp tục...")
                
                # Kiểm tra dừng sau khi goto
                self._check_stop()
                
                # Bước 2: Đợi load xong trang thì Bấm Dự án mới
                try:
                    new_project_btn = page.locator('[data-type="button-overlay"]').first
                    new_project_btn.wait_for(state="visible", timeout=15000)
                    new_project_btn.click()
                    print(f"[Cảnh {self.index}] Đã bấm Dự án mới.")
                except Exception as e:
                    print(f"[Cảnh {self.index}] Lỗi khi bấm Dự án mới: {e}")
                
                # Kiểm tra popup "Bắt đầu" (nếu có)
                try:
                    start_btn = page.locator('button:has-text("Bắt đầu")').last
                    start_btn.wait_for(state="visible", timeout=3000)
                    start_btn.click()
                    print(f"[Cảnh {self.index}] Đã bấm 'Bắt đầu' từ thông báo popup.")
                except Exception:
                    pass

                # Kiểm tra dừng
                self._check_stop()
                
                # Xử lý popup thông báo cookie nếu có (giữ nguyên cấu trúc xử lý popup chung)
                try:
                    close_btn = page.locator('button[aria-label="Close"], button[aria-label="Đóng"], [role="dialog"] button:has(svg)').first
                    close_btn.wait_for(state="visible", timeout=3000)
                    close_btn.click()
                    print(f"[Cảnh {self.index}] Đã đóng popup thông báo.")
                except PlaywrightTimeoutError:
                    pass
                except Exception:
                    pass
                
                # Bước 3: Cấu hình thông số (Video, Khung hình, 9:16, 1x, Veo 3.1-Lite)
                try:
                    print(f"[Cảnh {self.index}] Bắt đầu cấu hình thông số tạo video...")
                    # Mở menu cài đặt - Nút này có text thay đổi tuỳ theo cấu hình hiện tại (vd: "Nano Banana 2 x2" hoặc "Video 1x")
                    setting_btn = page.locator('button', has_text=re.compile(r'1x|x2|x3|x4|Video|Hình ảnh|Veo|Imagen|Nano', re.IGNORECASE)).last
                    setting_btn.click(timeout=10000)
                    if self._stop_event.wait(1): raise InterruptedError("Đã dừng")
                    
                    try:
                        page.locator('button:has-text("Video")').first.click(timeout=2000)
                    except: pass
                    try:
                        page.locator('button:has-text("Khung hình")').last.click(timeout=2000)
                    except: pass
                    try:
                        page.locator('button:has-text("9:16")').last.click(timeout=2000)
                    except: pass
                    try:
                        page.locator('button:has-text("1x")').first.click(timeout=2000)
                    except: pass
                    try:
                        try:
                            page.locator('text="Veo 3.1 - Lite"').last.click(timeout=2000)
                        except:
                            model_btn = page.locator('button:has-text("Veo"), button:has-text("Imagen")').last
                            model_btn.click(timeout=2000)
                            if self._stop_event.wait(0.5): raise InterruptedError("Đã dừng")
                            page.locator('text="Veo 3.1 - Lite"').last.click(timeout=2000)
                    except: pass
                    
                    print(f"[Cảnh {self.index}] Đã cấu hình xong thông số.")
                except Exception as e:
                    if isinstance(e, InterruptedError): raise
                    print(f"[Cảnh {self.index}] Lỗi cấu hình thông số: {e}")
                finally:
                    # Luôn luôn đảm bảo thoát khỏi menu cài đặt để không che khuất ô nhập prompt
                    page.keyboard.press("Escape")
                    if self._stop_event.wait(1): raise InterruptedError("Đã dừng")
                    
                self._check_stop()
                
                # Bước 4: Sau khi thiết lập xong thì gõ vào prompt của dự án đó
                try:
                    search_input = page.get_by_placeholder(re.compile(r"tạo gì|create", re.IGNORECASE)).first
                    search_input.wait_for(state="visible", timeout=5000)
                except:
                    search_input = page.locator('textarea:visible, div[contenteditable="true"]:visible').last
                
                search_input.wait_for(state="visible", timeout=15000)
                search_input.click(force=True)  # force=True để tránh bị chặn bởi các element khác
                
                # Điền prompt siêu tốc (thay vì type từng chữ)
                search_input.fill(self.prompt_text)
                
                # Bước 4.1: Sau khi gõ prompt xong thì bấm nút gửi để tạo video
                if self._stop_event.wait(1): raise InterruptedError("Đã dừng")
                
                # Với ô nhập liệu nhiều dòng (textarea), thường phải dùng Control+Enter để gửi thay vì Enter
                search_input.press("Control+Enter")
                print(f"[Cảnh {self.index}] Đã bấm gửi (Ctrl+Enter), đang chờ AI tạo video...")
                
                # Bước 5: Đợi quá trình tạo video hoàn tất (dù thành công hay lỗi)
                try:
                    print(f"[Cảnh {self.index}] Bắt đầu theo dõi quá trình tạo (chờ tối đa 5 phút)...")
                    timeout = 300  # 300 giây
                    elapsed = 0
                    has_started_generating = False
                    disappear_count = 0  # Biến đếm thời gian khi mất tiến trình
                    
                    while elapsed < timeout:
                        self._check_stop()
                        
                        if elapsed == 4 and not has_started_generating:
                            print(f"[Cảnh {self.index}] Vẫn chưa thấy bắt đầu tạo, thử click trực tiếp nút Gửi dự phòng...")
                            try:
                                # Thường nút Mũi tên gửi là nút button nằm cuối cùng trên giao diện
                                page.locator('button').last.click(timeout=1500)
                            except:
                                pass

                        # 1. Kiểm tra xem có bảng báo lỗi không (Không thành công / Rất tiếc)
                        error_locator = page.locator('text="Không thành công", text="đã xảy ra lỗi", text="Rất tiếc"').first
                        if error_locator.is_visible():
                            print(f"[Cảnh {self.index}] Quá trình tạo kết thúc sớm (Hệ thống báo lỗi).")
                            break
                            
                        # 2. Kiểm tra dấu hiệu đang tạo (thường có chữ 1%, 5%... hoặc progressbar)
                        progress_locator = page.locator('text=/^\\d{1,3}%$/').first
                        progressbar_locator = page.get_by_role("progressbar").first
                        queue_locator = page.locator('text="Đang trong hàng đợi", text="In queue"').first
                        
                        is_generating = progress_locator.is_visible() or progressbar_locator.is_visible() or queue_locator.is_visible()
                        
                        if is_generating:
                            if not has_started_generating:
                                print(f"[Cảnh {self.index}] Hệ thống đang trong quá trình render video...")
                            has_started_generating = True
                            disappear_count = 0  # Đặt lại bộ đếm khi thấy tiến trình
                        else:
                            # Nếu không thấy dấu hiệu đang tạo nữa
                            if has_started_generating:
                                disappear_count += 2
                                if disappear_count >= 16:  # Chờ 16 giây để chắc chắn tiến trình không quay lại
                                    print(f"[Cảnh {self.index}] Đã tạo xong video (tiến trình render đã biến mất hoàn toàn).")
                                    break
                                else:
                                    print(f"[Cảnh {self.index}] Tạm thời mất dấu tiến trình render, chờ thêm... ({disappear_count}/16s)")
                            else:
                                # Nếu chờ hơn 60s mà vẫn chưa thấy bắt đầu tạo -> thoát để tránh treo luồng
                                if elapsed > 60:
                                    print(f"[Cảnh {self.index}] Cảnh báo: Không thấy tiến trình tạo sau 60s.")
                                    break
                                    
                        if self._stop_event.wait(2): raise InterruptedError("Đã dừng")
                        elapsed += 2
                        
                except Exception as e:
                    if isinstance(e, InterruptedError): raise
                    print(f"[Cảnh {self.index}] Lỗi trong vòng lặp chờ kết quả: {e}")
                
                # Bước 6: Sau khi video tạo xong thì tải video về máy
                try:
                    print(f"[Cảnh {self.index}] Đang tiến hành tải video về máy...")
                    
                    # Chuẩn bị tên file và thư mục
                    save_dir = os.path.join(os.getcwd(), "videos_da_tao")
                    if not os.path.exists(save_dir):
                        os.makedirs(save_dir)
                        
                    safe_prompt = re.sub(r'[\\/*?:"<>|]', "", self.prompt_text)
                    safe_prompt = safe_prompt[:30].strip() if safe_prompt else "video"
                    filename = f"canh_{self.index}_{safe_prompt}.mp4"
                    save_path = os.path.join(save_dir, filename)

                    # Cách 1: Thử dùng Javascript để đọc dữ liệu video (Fetch Blob -> Base64)
                    js_success = False
                    try:
                        has_video = page.evaluate("!!document.querySelector('video')")
                        if has_video:
                            print(f"[Cảnh {self.index}] Tìm thấy thẻ video, đang trích xuất dữ liệu trực tiếp...")
                            # Tăng timeout cho đoạn JS fetch (video có thể lớn)
                            base64_data = page.evaluate("""
                                async () => {
                                    let v = document.querySelector('video');
                                    let url = v.src || (v.querySelector('source') ? v.querySelector('source').src : null);
                                    if (!url) throw new Error("Không tìm thấy URL trong thẻ video");
                                    
                                    // Fetch dữ liệu từ blob hoặc url
                                    let response = await fetch(url);
                                    let blob = await response.blob();
                                    
                                    // Chuyển blob thành chuỗi base64
                                    return new Promise((resolve, reject) => {
                                        let reader = new FileReader();
                                        reader.onloadend = () => resolve(reader.result);
                                        reader.onerror = reject;
                                        reader.readAsDataURL(blob);
                                    });
                                }
                            """)
                            
                            if base64_data and "," in base64_data:
                                import base64
                                header, encoded = base64_data.split(",", 1)
                                with open(save_path, "wb") as f:
                                    f.write(base64.b64decode(encoded))
                                print(f"[Cảnh {self.index}] ✅ Đã lưu video thành công (qua JS fetch): {save_path}")
                                js_success = True
                    except Exception as js_err:
                        print(f"[Cảnh {self.index}] JS extraction thất bại ({js_err}), chuyển sang tìm nút UI...")

                    # Cách 2: Nếu JS thất bại, tìm nút tải xuống trên giao diện
                    if not js_success:
                        # Hover vào thẻ video để hiện các nút chức năng (nếu có)
                        try:
                            page.locator('video, [data-type="video-result"], [role="application"]').last.hover(timeout=3000)
                            page.wait_for_timeout(1000)
                        except:
                            pass
                        
                        # Thử click nút 3 chấm (More options) nếu nút Tải bị ẩn bên trong
                        try:
                            more_options = page.locator('button[aria-label="Tùy chọn khác"], button[aria-label="More options"], button:has-text("⋮")').last
                            if more_options.is_visible():
                                more_options.click(timeout=2000)
                                page.wait_for_timeout(1000)
                        except:
                            pass

                        # Các selector phổ biến của nút Tải xuống
                        locators = [
                            'button[aria-label*="ownload"]',
                            'button[aria-label*="ải xuống"]',
                            '[role="button"][aria-label*="ownload"]',
                            '[role="button"][aria-label*="ải xuống"]',
                            'button[title*="ownload"]',
                            'button[title*="ải xuống"]',
                            'a[download]',
                            'button:has-text("Tải xuống")',
                            'button:has-text("Lưu")',
                            'button:has-text("Save")',
                            '[data-action="download"]'
                        ]
                        download_btn = page.locator(", ".join(locators)).first
                        
                        print(f"[Cảnh {self.index}] Đang chờ nhấp vào nút tải trên giao diện...")
                        with page.expect_download(timeout=20000) as download_info:
                            download_btn.click(timeout=10000, force=True)
                        
                        download = download_info.value
                        download.save_as(save_path)
                        print(f"[Cảnh {self.index}] ✅ Đã tải video thành công (qua nút UI): {save_path}")

                except Exception as e:
                    print(f"[Cảnh {self.index}] ❌ Lỗi khi tải video: {e}")

                # Bước 7: Đợi thêm 55 giây rồi mới kết thúc quy trình
                print(f"[Cảnh {self.index}] Đang đợi thêm 55 giây sau khi tải xong...")
                if self._stop_event.wait(55): raise InterruptedError("Đã dừng")
                
                # Sau khi xong, đánh dấu hoàn thành và đóng browser
                status = "Hoàn thành"
                print(f"[Cảnh {self.index}] ✅ Đã gõ prompt xong và đợi 55s, chuẩn bị đóng browser")
                browser.close()
                
        except InterruptedError:
            print(f"[Cảnh {self.index}] Luồng đã bị ngắt bởi người dùng.")
            status = "Đã dừng"
            response_data = "-"
        except Exception as e:
            import traceback
            print(f"[Cảnh {self.index}] EXCEPTION:\n{traceback.format_exc()}")
            status = f"Lỗi: {e}"
            response_data = "-"
        finally:
            # 5. Dọn dẹp: Đóng profile GPM sau khi làm xong
            if profile_id:
                self._cleanup_profile(gpm, profile_id)
            
            if status:
                self.record.emit(self.index, status, response_data)

    def _cleanup_profile(self, gpm, profile_id):
        close_attempts = 3
        close_success = False
        for attempt in range(1, close_attempts + 1):
            try:
                response = gpm.close_profile(apiurl_Gpm=self.api_url_gpm, id_profile=profile_id)
                if isinstance(response, dict) and response.get("success") is False:
                    raise RuntimeError(f"close_profile trả về lỗi: {response}")
                print(f"[Cảnh {self.index}] ✅ Đã close_profile: {profile_id} (lần {attempt})")
                close_success = True
                break
            except Exception as e:
                print(f"[Cảnh {self.index}] ⚠️ close_profile thất bại lần {attempt}: {e}")
                time.sleep(2)
        if not close_success:
            print(f"[Cảnh {self.index}] ⚠️ close_profile không thành công sau {close_attempts} lần.")
        return True

    def stop(self):
        """Ra hiệu dừng: đặt cả boolean lẫn Event để ngắt sleep ngay lập tức."""
        self.is_running = False
        self._stop_event.set()  # Ngắt bất kỳ _stop_event.wait() nào đang bị block

    def _check_stop(self):
        """Kiểm tra và ném lỗi nếu người dùng yêu cầu dừng."""
        if not self.is_running:
            raise InterruptedError("Đã dừng")

class PromptApiThread(QThread):
    result_ready = pyqtSignal(object)

    def __init__(self, payload, post_url):
        super().__init__()
        self.payload = payload
        self.post_url = post_url

    def run(self):
        result_data = None
        # 1. Gọi API POST để gửi thông tin lên N8N
        try:
            print(f"[Phân tích Prompt] Đang gọi API POST: {self.post_url}")
            response_post = requests.post(self.post_url, json=self.payload, timeout=60)
            print(f"[Phân tích Prompt] Đã gửi POST thành công - Status: {response_post.status_code}")
            if response_post.status_code == 200:
                try:
                    result_data = response_post.json()
                except ValueError:
                    print("[Phân tích Prompt] Lỗi parse JSON từ API")
        except Exception as e:
            print(f"[Phân tích Prompt] Lỗi gọi API POST N8N: {e}")
                
        self.result_ready.emit(result_data)

class Manager(QtWidgets.QMainWindow, Ui_Widget):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        self.config = {}  # Dictionary để lưu config values không phải widget
        self.config_path = os.path.join(os.path.dirname(__file__), "config.env")
        self._config_map = {
            "AI_MODEL": self.cb_ai_model,
            "ASYNC_API_KEY": self.le_api_key,
            "API_URL_GPM": self.le_api_url_gpm,
            "BROWSER": self.cb_browser,
            "WIN_SIZE": self.cb_win_size,
            "VIDEO_FOLDER": self.le_folder,
            "STYLE": self.cb_style,
            "LANGUAGE": self.cb_language,
            "COPY_RATIO": self.cb_copy_ratio,
            "SCENE_COUNT": self.cb_scene_count,
            "VOICE_DESC": self.te_voice_desc,
            "PROXY_LIST": self.te_proxy_input,
            "GROK_LANG": self.grok_cb_lang,
            "GROK_LINK": self.grok_le_link,
            "GROK_DESC": self.grok_le_desc,
            "VEO3_LANG": self.veo3_cb_lang,
            "VEO3_LINK": self.veo3_le_link,
            "VEO3_DESC": self.veo3_le_desc,
            "SEED_LANG": self.seed_cb_lang,
            "SEED_LINK": self.seed_le_link,
            "SEED_DESC": self.seed_le_desc,
            "KOL_LANG": self.kol_cb_lang,
            "KOL_DESC": self.kol_le_desc,
            "KOL_PROMPT": self.kol_le_prompt,
        }

        self._load_config()
        self.scene_prompt_boxes = self.tab_veo3.findChildren(QtWidgets.QTextEdit, "promptBox")
        self._connect_config_signals()

        self.threads = []
        self.completed_threads = 0
        self.total_threads = 0
        self.running_threads = 0    # Số luồng đang chạy thực tế
        self._is_stopping = False   # Flag để bỏ qua signal của thread khi đã bấm dừng

        # Kết nối nút proxy: mở rộng / thu gọn bảng nhập proxy
        self.btn_proxy_collapsed.clicked.connect(self._toggle_proxy_panel)
        self.btn_proxy_close.clicked.connect(self._toggle_proxy_panel)

        self.prompt_thread = None
        
        # Thêm timer và biến cho hiệu ứng "Đang xử lý"
        self.loading_timer = QtCore.QTimer(self)
        self.loading_timer.timeout.connect(self._animate_loading_button)
        self.dot_count = 0
        self.is_prompt_running = False
        self.prompt_scene_count = 0

        # Kết nối sự kiện Click cho nút "BẮT ĐẦU TẠO VIDEO" bên tab Veo3
        self.veo3_btn_analyze.clicked.connect(self.startThreadVeo3)
        
        # Kết nối sự kiện Click cho nút "Bắt đầu phân tích tạo Prompt" cho cả 2 tab
        self.veo3_btn_merge.clicked.connect(self.analyzePrompts)
        self.kol_btn_merge.clicked.connect(self.analyzePrompts)
        
        # Kết nối nút cập nhật phiên bản cho cả hai tab
        self.veo3_btn_update.clicked.connect(self._update_version)
        self.kol_btn_update.clicked.connect(self._update_version)

    def _animate_loading_button(self):
        self.dot_count = (self.dot_count + 1) % 4
        dots = "." * self.dot_count
        
        if self.is_prompt_running:
            text = f"⏱ Đang xử lý {self.prompt_scene_count} cảnh{dots}"
            self.veo3_btn_running.setText(text)
            self.kol_btn_running.setText(text)
        elif self.running_threads > 0:
            text = f"⏱ Đang xử lý {self.running_threads} cảnh{dots}"
            self.veo3_btn_running.setText(text)
            self.kol_btn_running.setText(text)

    def _start_btn_running_animation(self):
        if not self.loading_timer.isActive():
            self.dot_count = 0
            self.loading_timer.start(500)
            style = (
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #d97706, stop:1 #b45309);"
                "border: 1px solid #d97706;"
                "color: white; font-weight: bold; border-radius: 6px;"
            )
            self.veo3_btn_running.setStyleSheet(style)
            self.kol_btn_running.setStyleSheet(style)

    def _stop_btn_running_animation(self):
        if not self.is_prompt_running and self.running_threads == 0:
            self.loading_timer.stop()
            self.veo3_btn_running.setStyleSheet("")
            self.kol_btn_running.setStyleSheet("")
            self.veo3_btn_running.setText("⏱ Đang xử lý 0 cảnh")
            self.kol_btn_running.setText("⏱ Đang xử lý 0 cảnh")

    def _set_veo3_btn_running(self, is_running):
        """Đổi trạng thái nút BẮT ĐẦU TẠO VIDEO theo trạng thái luồng."""
        if is_running:
            self.veo3_btn_analyze.setText("⏹  ĐANG CHẠY TẠO VIDEO...BẤM ĐỂ DỪNG CHẠY")
            self.veo3_btn_analyze.setStyleSheet(
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                " stop:0 #b91c1c, stop:1 #ef4444);"
                " border: none; color: white; font-weight: bold;"
            )
            self._start_btn_running_animation()
        else:
            self.veo3_btn_analyze.setText("🚀  Bắt đầu tạo video từ tất cả cảnh")
            # Xoá style inline → fallback về QSS gốc (#analyzeBtn)
            self.veo3_btn_analyze.setStyleSheet("")
            self._stop_btn_running_animation()

    def _stop_all_veo3_threads(self):
        """Dừng tất cả luồng đang chạy và reset UI ngay lập tức."""
        self._is_stopping = True  # Báo hiệu cho update_data bỏ qua signal từ giờ này
        for t in self.threads:
            t.stop()
        print("[Manager] ⛔ Đã gửi lệnh dừng tất cả luồng.")
        # Reset UI ngay lập tức — không chờ thread kết thúc
        self.running_threads = 0
        self.completed_threads = self.total_threads  # Đánh dấu đã xong để tránh lệch counter
        self._set_veo3_btn_running(False)
        QtWidgets.QApplication.processEvents()  # Buộc UI render ngay

    def _set_prompt_btn_running(self, is_running):
        """Đổi trạng thái nút Bắt đầu phân tích tạo Prompt."""
        if is_running:
            self.is_prompt_running = True
            text = "⏳ Đang phân tích tạo prompt..."
            style = (
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                " stop:0 #9333ea, stop:1 #d8b4fe);"
                " border: none; color: white; font-weight: bold;"
            )
            self.veo3_btn_merge.setText(text)
            self.veo3_btn_merge.setStyleSheet(style)
            self.kol_btn_merge.setText(text)
            self.kol_btn_merge.setStyleSheet(style)
            self._start_btn_running_animation()
        else:
            self.is_prompt_running = False
            text = "🎬 Bắt đầu phân tích tạo Prompt"
            self.veo3_btn_merge.setText(text)
            self.veo3_btn_merge.setStyleSheet("")
            self.kol_btn_merge.setText(text)
            self.kol_btn_merge.setStyleSheet("")
            self._stop_btn_running_animation()

    def analyzePrompts(self):
        try:
            input_soluong = int(self.cb_scene_count.currentText())
        except ValueError:
            QMessageBox.warning(self, "Lỗi", "Số lượng cảnh không hợp lệ.")
            return

        if input_soluong <= 0:
            return

        # Khởi chạy thread để call API không làm đơ UI
        if self.prompt_thread and self.prompt_thread.isRunning():
            QMessageBox.warning(self, "Cảnh báo", "Đang phân tích tạo prompt, vui lòng đợi!")
            return

        api_url = "https://thangdepzai.devttt.com/webhook/webhook_get_data_tool"
        print(f"[Manager] Bắt đầu gọi 1 API POST...")
        self.prompt_scene_count = input_soluong
        self._set_prompt_btn_running(True)
        self._animate_loading_button()
        QtWidgets.QApplication.processEvents()

        # Thu thập 9 thông tin từ UI
        payload = {
            "link_youtube": self.veo3_le_link.text().strip() if hasattr(self, 'veo3_le_link') else "",
            "mo_ta_them": self.veo3_le_desc.text().strip() if hasattr(self, 'veo3_le_desc') else "",
            "mo_hinh_sinh_kich_ban": self.cb_ai_model.currentText(),
            "asynclab_api_key": self.le_api_key.text().strip(),
            "phong_cach": self.cb_style.currentText(),
            "ngon_ngu": self.cb_language.currentText(),
            "ty_le_copy": self.cb_copy_ratio.currentText(),
            "giong_nhan_vat": self.te_voice_desc.toPlainText().strip(),
            "so_canh": input_soluong
        }

        self.prompt_thread = PromptApiThread(payload, api_url)
        self.prompt_thread.result_ready.connect(self._on_prompt_thread_finished)
        self.prompt_thread.start()

    def _on_prompt_thread_finished(self, result_data):
        self._set_prompt_btn_running(False)
        
        # Hàm đệ quy tìm dict chứa các prompt, phòng trường hợp webhook bọc dữ liệu nhiều lớp (ví dụ: [{"Content": "{\\"prompt_1\\":...}"}])
        def find_prompt_dict(data):
            if isinstance(data, dict):
                if "prompt_1" in data:
                    return data
                for v in data.values():
                    res = find_prompt_dict(v)
                    if res: return res
            elif isinstance(data, list):
                for item in data:
                    res = find_prompt_dict(item)
                    if res: return res
            elif isinstance(data, str):
                import json
                try:
                    parsed = json.loads(data)
                    return find_prompt_dict(parsed)
                except:
                    pass
            return None

        prompt_dict = find_prompt_dict(result_data)
        
        if prompt_dict:
            # Lấy tab đang active để update (0 = Veo3, 1 = KOL)
            current_tab_index = self.tabWidget.currentIndex()
            if current_tab_index == 0:
                target_tab = self.tab_veo3
            else:
                target_tab = self.tab_kol
                
            # Tìm tất cả QTextEdit có objectName là "promptBox" trong tab hiện tại
            prompt_boxes = target_tab.findChildren(QtWidgets.QTextEdit, "promptBox")
            
            # Update text cho từng prompt box tương ứng với số cảnh
            updated_count = 0
            for i, p_box in enumerate(prompt_boxes):
                prompt_key = f"prompt_{i+1}"
                if prompt_key in prompt_dict:
                    p_box.setPlainText(prompt_dict[prompt_key])
                    updated_count += 1
            
            if updated_count > 0:
                QMessageBox.information(self, "Thành công", f"Đã cập nhật {updated_count} Prompt thành công lên giao diện!")
            else:
                QMessageBox.warning(self, "Lỗi", "Tìm thấy cấu trúc Prompt nhưng không khớp với các cảnh trên giao diện!")
        else:
            QMessageBox.warning(self, "Lỗi", "Không nhận được dữ liệu Prompt hợp lệ từ API (không tìm thấy prompt_1)!")

    def startThreadVeo3(self):
        # Nếu đang có luồng chạy → bấm nút = dừng tất cả
        if any(t.isRunning() for t in self.threads):
            self._stop_all_veo3_threads()
            return

        # Reset flag dừng trước mỗi lần chạy mới
        self._is_stopping = False

        # Lấy số lượng "cảnh" (số luồng) từ combobox giao diện góc trái
        try:
            input_soluong = int(self.cb_scene_count.currentText())
        except ValueError:
            QMessageBox.warning(self, "Lỗi", "Số lượng cảnh không hợp lệ.")
            return

        # Giới hạn tối đa 10 luồng
        if input_soluong > 10:
            QMessageBox.warning(self, "Lỗi", "Số lượng cảnh tối đa là 10.")
            return

        # Lấy API URL GPM từ ô nhập liệu trên giao diện
        input_api_url_gpm = self.le_api_url_gpm.text().strip()
        if not input_api_url_gpm:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập API URL GPM (ví dụ: http://localhost:9495).")
            return

        # Đọc danh sách Profile ID từ ô nhập thủ công trên giao diện
        # Giữ nguyên vị trí từng dòng: luồng i lấy ID dòng i
        profile_id_list = []
        proxy_text = self.te_proxy_input.toPlainText().strip()
        if proxy_text:
            for line in proxy_text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    profile_id_list.append("")
                else:
                    profile_id_list.append(line)
        valid_count = sum(1 for p in profile_id_list if p)
        print(f"[Manager] Đọc được {valid_count}/{len(profile_id_list)} ID Profile.")

        # Kiểm tra số lượng Profile và định dạng Profile ID
        invalid_profile_format = any(
            p and not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", p)
            for p in profile_id_list
        )
        if valid_count < input_soluong or invalid_profile_format:
            QMessageBox.warning(
                self,
                "Kiểm tra lại danh sách Profile",
                "Vui lòng kiểm tra lại danh sách Profile. Số Profile đang ít hơn số cảnh hoặc định dạng điền trong danh sách Profile không đúng"
            )
            return

        self.total_threads = input_soluong
        self.completed_threads = 0
        self.running_threads = input_soluong  # Khởi đầu: tất cả luồng đều chạy
        
        # Đổi nút sang trạng thái đang chạy NGAY lập tức trước khi khởi động luồng
        self._set_veo3_btn_running(True)
        self._animate_loading_button()
        QtWidgets.QApplication.processEvents()  # Buộc UI render ngay

        self.threads = []

        input_win_size_raw = self.cb_win_size.currentText()
        input_win_size = input_win_size_raw.replace("px", "").replace(":", ",")
        
        try:
            win_w, win_h = map(int, input_win_size.split(","))
        except:
            win_w, win_h = 800, 800

        # Lấy kích thước màn hình
        screen_rect = QtWidgets.QApplication.desktop().screenGeometry()
        screen_width = screen_rect.width()
        cols = max(1, screen_width // win_w)

        # Khởi tạo GPM theo đúng số "cảnh" đã chọn
        for i in range(1, input_soluong + 1):
            # Lấy Profile ID theo index (dòng i-1 trong ô nhập)
            profile_id = profile_id_list[i - 1] if (i - 1) < len(profile_id_list) else ""
            
            # Tính toán vị trí hiển thị luồng
            idx = i - 1
            col = idx % cols
            row = idx // cols
            pos_x = col * win_w
            pos_y = row * win_h
            win_pos = f"{pos_x},{pos_y}"
            
            # Lấy nội dung prompt hiển thị trên giao diện của cảnh tương ứng
            current_prompt = "test cảnh"
            if (i - 1) < len(self.scene_prompt_boxes):
                text = self.scene_prompt_boxes[i - 1].toPlainText().strip()
                if text:
                    current_prompt = text
                    
            thread = MultiThread(
                index=i,
                api_url_gpm=input_api_url_gpm,
                profile_id=profile_id,
                prompt_text=current_prompt,
                win_size=input_win_size,
                win_pos=win_pos
            )

            # Lắng nghe dữ liệu bắn về từ Thread để update log
            thread.record.connect(self.update_data)
            self.threads.append(thread)
            thread.start()
            
            # Delay 1.5 giây giữa các luồng để tránh spam API GPM và treo máy
            time.sleep(1.5)

    def update_data(self, index, status, response_data):
        # Nếu đang trong trạng thái dừng thủ công → bỏ qua tín hiệu từ thread
        if self._is_stopping:
            print(f"[Cảnh {index}] Bỏ qua signal (tool đã dừng): {status}")
            return

        # In log ra màn hình console để theo dõi
        print(f"[Cảnh {index}] Trạng thái: {status} | Phản hồi N8N: {response_data}")
        
        # Nếu luồng hoàn tất, bị lỗi, hoặc bị dừng thì giảm bộ đếm luồng đang chạy
        if status == "Hoàn thành" or status.startswith("Lỗi") or status == "Đã dừng":
            if status == "Hoàn thành" and response_data and response_data != "-":
                # Cập nhật text prompt cho cảnh tương ứng nếu có dữ liệu trả về
                if 1 <= index <= len(self.scene_prompt_boxes):
                    self.scene_prompt_boxes[index - 1].setPlainText(response_data)

            self.completed_threads += 1
            self.running_threads = max(0, self.running_threads - 1)  # Giảm luồng đang chạy
            self._animate_loading_button()
            
            # Nếu tất cả các luồng đã xong (hoàn thành / lỗi / dừng)
            if self.completed_threads == self.total_threads:
                self.running_threads = 0
                # Trả text nút về trạng thái ban đầu
                self._set_veo3_btn_running(False)
                if status != "Đã dừng":  # Chỉ thông báo "Thành công" nếu không phải dừng thủ công
                    QMessageBox.information(self, "Thành công", f"Đã chạy xong toàn bộ {self.total_threads} cảnh!")

    def _connect_config_signals(self):
        for widget in self._config_map.values():
            if isinstance(widget, QtWidgets.QComboBox):
                widget.currentTextChanged.connect(self._save_config)
            elif isinstance(widget, QtWidgets.QLineEdit):
                widget.textChanged.connect(self._save_config)
            elif isinstance(widget, QtWidgets.QTextEdit):
                widget.textChanged.connect(self._save_config)
        self.tabWidget.currentChanged.connect(self._save_config)

    def _load_config(self):
        values = self._read_env_file()
        for key, widget in self._config_map.items():
            if key not in values:
                continue
            value = self._decode_env_value(values[key])
            if isinstance(widget, QtWidgets.QComboBox):
                idx = widget.findText(value)
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            elif isinstance(widget, QtWidgets.QTextEdit):
                widget.setPlainText(value)
            else:
                widget.setText(value)

        # Load version từ config
        if "VERSION" in values:
            self.config["VERSION"] = self._decode_env_value(values["VERSION"])
        else:
            self.config["VERSION"] = "1.0"

        if "CURRENT_TAB" in values:
            try:
                tab_index = int(values["CURRENT_TAB"])
                if 0 <= tab_index < self.tabWidget.count():
                    self.tabWidget.setCurrentIndex(tab_index)
            except ValueError:
                pass

        self._save_config()

    def _read_env_file(self):
        if not os.path.exists(self.config_path):
            return {}
        data = {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                data[key.strip()] = val.strip()
        return data

    def _decode_env_value(self, value):
        return value.replace("\\n", "\n")

    def _encode_env_value(self, value):
        return value.replace("\n", "\\n")

    def _save_config(self):
        env_data = {}
        for key, widget in self._config_map.items():
            if isinstance(widget, QtWidgets.QComboBox):
                env_data[key] = widget.currentText()
            elif isinstance(widget, QtWidgets.QTextEdit):
                env_data[key] = widget.toPlainText()
            else:
                env_data[key] = widget.text()
        
        # Lưu VERSION từ config dictionary
        env_data["VERSION"] = self.config.get("VERSION", "1.0")
        env_data["CURRENT_TAB"] = str(self.tabWidget.currentIndex())

        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("# Auto-generated config for tool UI values\n")
            for key, value in env_data.items():
                f.write(f"{key}={self._encode_env_value(value)}\n")

    def _toggle_proxy_panel(self):
        """Thu gọn hoặc mở rộng bảng nhập Profile."""
        is_expanded = self.proxy_expand_panel.isVisible()
        if is_expanded:
            # Đóng panel → cập nhật text nút thu gọn
            self.proxy_expand_panel.setVisible(False)
            count = sum(
                1 for line in self.te_proxy_input.toPlainText().splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
            if count:
                self.btn_proxy_collapsed.setText(f"📋  {count} Profile — Nhấp để chỉnh sửa")
            else:
                self.btn_proxy_collapsed.setText("📋  Nhấp để nhập danh sách Profile")
        else:
            # Mở panel
            self.proxy_expand_panel.setVisible(True)

    def _update_version(self):
        """Cho phép người dùng cập nhật phiên bản tool."""
        from PyQt5 import QtWidgets
        
        # Danh sách phiên bản có sẵn
        available_versions = ["1.0", "1.1", "1.2", "1.3"]
        
        # Lấy phiên bản hiện tại từ config
        current_version = self.config.get("VERSION", "1.0")
        
        # Tìm index của phiên bản hiện tại
        current_index = available_versions.index(current_version) if current_version in available_versions else 0
        
        # Hiển thị dialog chọn phiên bản
        selected_version, ok = QtWidgets.QInputDialog.getItem(
            self, 
            "Cập nhật phiên bản", 
            "Chọn phiên bản:",
            available_versions,
            current_index,
            editable=False
        )
        
        if ok and selected_version.strip():
            # Cập nhật config
            self.config["VERSION"] = selected_version.strip()
            
            # Cập nhật UI trên cả hai tab
            # Tìm tất cả QLabel với objectName "verLabel" và cập nhật text
            for widget in self.findChildren(QtWidgets.QLabel):
                if widget.objectName() == "verLabel":
                    widget.setText(f"Phiên bản {selected_version.strip()}")
            
            # Lưu config vào file
            self._save_config()

    def closeEvent(self, event):
        # Stop và dọn dẹp các Thread khi ấn X tắt phần mềm
        for t in self.threads:
            t.stop()
            if not t.wait(5000):
                print(f"[Manager] Thread {t.index} vẫn đang chạy khi đóng ứng dụng.")
        event.accept()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = Manager()
    window.resize(1920, 1245)
    window.show()
    sys.exit(app.exec_())