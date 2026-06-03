import sys
import re
import os
import time
import threading
import base64
import requests

# Thoi gian cho toi da khi goi API tao anh tham chieu. API tra nhanh hon thi chay tiep ngay.
REFERENCE_IMAGE_API_MAX_TIMEOUT = 30 * 60
REFERENCE_IMAGE_API_MAX_RETRIES = 3
REFERENCE_IMAGE_API_RETRY_DELAY_SECONDS = 20
REFERENCE_IMAGE_API_RETRY_STATUS_CODES = {502, 503, 504}
REFERENCE_IMAGE_GENERATION_MAX_TIMEOUT = 10 * 60
REFERENCE_IMAGE_GENERATION_START_TIMEOUT = 120
REFERENCE_IMAGE_PROMPT_WEBHOOK_URL = "https://n8n.aiplt.io.vn/webhook/webhook_get_data_tool"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if "--self-test-moviepy" in sys.argv:
    try:
        import importlib.metadata as package_metadata
        import imageio
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.compositing.CompositeVideoClip import concatenate_videoclips
        import imageio_ffmpeg

        package_metadata.version("imageio")
        package_metadata.version("imageio-ffmpeg")
        package_metadata.version("moviepy")
        imageio_ffmpeg.get_ffmpeg_exe()
        sys.exit(0)
    except Exception as exc:
        print(f"moviepy self-test failed: {exc}")
        sys.exit(70)

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QMessageBox
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError


# Import các file phụ trợ
from tool_video_ai_layout_3_UI import Ui_Widget
from GpmGlobalApi_tuviet import Gpm
from kie_ai_auto import setup_kie_ai_connections

class MultiThread(QThread):
    # Khai báo signal trả về: (Số thứ tự cảnh, trạng thái, kết quả)
    record = pyqtSignal(int, str, str)

    def __init__(self, index, api_url_gpm, profile_id="", prompt_text="test cảnh", win_size="800,800", win_pos="0,0", flow_settings=None, save_dir="", reference_image_path=""):
        super().__init__()
        self.index = index
        self.api_url_gpm = api_url_gpm  # URL GPM lấy từ giao diện
        self.profile_id = profile_id    # ID Profile cho luồng này
        self.prompt_text = prompt_text
        self.win_size = win_size
        self.win_pos = win_pos
        self.save_dir = save_dir
        self.reference_image_path = reference_image_path
        self.flow_settings = flow_settings or {
            "content_type": "Video",
            "frame_type": "Khung hình",
            "aspect_ratio": "9:16",
            "gen_count": "1x",
            "ai_model": "Veo 3.1 - Lite"
        }
        self.is_running = True
        self._stop_event = threading.Event()  # Event để dừng sleep ngay lập tức

    def _upload_reference_image_to_flow(self, page, image_path):
        raw_image_path = str(image_path or "").strip()
        if not raw_image_path:
            raise RuntimeError("Chưa có hình tham chiếu hợp lệ để upload.")
        image_path = os.path.abspath(raw_image_path)
        if not os.path.exists(image_path):
            raise RuntimeError("Chưa có hình tham chiếu hợp lệ để upload.")

        self.record.emit(self.index, "Đang upload ảnh tham chiếu", os.path.basename(image_path))
        last_error = None

        def accept_upload_terms_if_visible():
            accept_selectors = [
                'button:has-text("Tôi đồng ý")',
                '[role="button"]:has-text("Tôi đồng ý")',
                'button:has-text("I agree")',
                '[role="button"]:has-text("I agree")',
                'button:has-text("Agree")',
                '[role="button"]:has-text("Agree")',
            ]
            for _ in range(8):
                self._check_stop()
                for accept_selector in accept_selectors:
                    try:
                        accept_btn = page.locator(accept_selector).last
                        if accept_btn.is_visible(timeout=500):
                            accept_btn.click(force=True, timeout=3000)
                            page.wait_for_timeout(1200)
                            print(f"[Cảnh {self.index}] Đã bấm xác nhận upload ảnh: {accept_selector}")
                            return True
                    except Exception:
                        pass
                page.wait_for_timeout(500)
            return False

        def wait_reference_image_ready():
            self.record.emit(self.index, "Đang chờ ảnh tham chiếu load xong", "-")
            stable_without_progress = 0
            saw_upload_progress = False

            for _ in range(90):
                self._check_stop()
                try:
                    progress_visible = page.locator(r'text=/^\d{1,3}%$/').first.is_visible(timeout=500)
                except Exception:
                    progress_visible = False

                try:
                    progressbar_visible = page.get_by_role("progressbar").first.is_visible(timeout=500)
                except Exception:
                    progressbar_visible = False

                try:
                    upload_text_visible = page.locator(
                        'text="Đang tải", text="Uploading", text="Processing", text="Đang xử lý"'
                    ).first.is_visible(timeout=500)
                except Exception:
                    upload_text_visible = False

                is_loading = progress_visible or progressbar_visible or upload_text_visible
                if is_loading:
                    saw_upload_progress = True
                    stable_without_progress = 0
                else:
                    stable_without_progress += 1
                    if stable_without_progress >= (3 if saw_upload_progress else 6):
                        page.wait_for_timeout(1000)
                        print(f"[Cảnh {self.index}] Ảnh tham chiếu đã load xong, tiếp tục nhập prompt.")
                        return True

                page.wait_for_timeout(1000)

            raise RuntimeError("Timeout: Ảnh tham chiếu chưa load xong sau 90 giây.")

        def try_file_inputs():
            nonlocal last_error
            try:
                file_inputs = page.locator('input[type="file"]')
                count = file_inputs.count()
            except Exception as e:
                last_error = e
                return False

            for input_index in range(count - 1, -1, -1):
                self._check_stop()
                try:
                    file_inputs.nth(input_index).set_input_files(image_path, timeout=5000)
                    page.wait_for_timeout(1500)
                    accept_upload_terms_if_visible()
                    wait_reference_image_ready()
                    print(f"[Cảnh {self.index}] Đã upload ảnh tham chiếu bằng input file: {image_path}")
                    return True
                except Exception as e:
                    last_error = e
            return False

        if try_file_inputs():
            return

        upload_triggers = [
            'button:has-text("Tác nhân")',
            '[role="button"]:has-text("Tác nhân")',
            'button:has-text("Thành phần")',
            '[role="button"]:has-text("Thành phần")',
            'button:has-text("Hình ảnh")',
            '[role="button"]:has-text("Hình ảnh")',
            'button:has-text("Ảnh")',
            '[role="button"]:has-text("Ảnh")',
            'button:has-text("Tải lên")',
            '[role="button"]:has-text("Tải lên")',
            'button:has-text("Image")',
            'button:has-text("Upload")',
            'button:has-text("Character")',
            'button[aria-label*="image" i]',
            'button[aria-label*="upload" i]',
            'button[aria-label*="attach" i]',
        ]

        for selector in upload_triggers:
            self._check_stop()
            try:
                trigger = page.locator(selector).last
                if not trigger.is_visible(timeout=1500):
                    continue
                with page.expect_file_chooser(timeout=5000) as chooser_info:
                    trigger.click(force=True, timeout=3000)
                chooser_info.value.set_files(image_path)
                page.wait_for_timeout(1500)
                accept_upload_terms_if_visible()
                wait_reference_image_ready()
                print(f"[Cảnh {self.index}] Đã upload ảnh tham chiếu qua nút: {selector}")
                return
            except Exception as e:
                last_error = e

        if try_file_inputs():
            return

        raise RuntimeError(f"Không thể upload ảnh tham chiếu vào Flow. Chi tiết: {last_error}")

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
                        if self._stop_event.wait(2):
                            raise InterruptedError("Đã dừng")
                if not browser:
                    raise RuntimeError("Không thể kết nối Playwright với trình duyệt.")
                
                context = browser.contexts[0]
                # Lấy tab mặc định hoặc tạo tab mới
                page = context.pages[0] if context.pages else context.new_page()
                
                # Áp dụng cứng viewport size cho Playwright để hiển thị chính xác
                try:
                    # Tự động trích xuất các con số từ chuỗi đầu vào (vd: "800px:1040px", "800, 1040", "800x1040")
                    numbers = re.findall(r'\d+', str(self.win_size))
                    if len(numbers) >= 2:
                        w, h = int(numbers[0]), int(numbers[1])
                        # Trừ đi khoảng 85px chiều cao cho thanh công cụ (Address bar + Title bar) của trình duyệt Chrome
                        # Viewport khớp đúng kích thước cửa sổ thực tế để hiển thị full
                        vp_w = w
                        vp_h = max(300, h - 85)
                        page.set_viewport_size({"width": vp_w, "height": vp_h})
                except Exception as e:
                    print(f"[Cảnh {self.index}] Lỗi set viewport: {e}")
                
                # Bước 1: vào trang https://labs.google/fx/vi/tools/flow
                try:
                    page.goto("https://labs.google/fx/vi/tools/flow", timeout=60000, wait_until="domcontentloaded")
                except PlaywrightTimeoutError:
                    print(f"[Cảnh {self.index}] goto timeout, tiếp tục...")
                
                # Tự động zoom trang web để nội dung hiển thị gần full trong viewport nhỏ
                # Tính zoom dựa trên viewport width: nếu nhỏ hơn 800px thì zoom ra để thấy nhiều hơn
                try:
                    numbers = re.findall(r'\d+', str(self.win_size))
                    if len(numbers) >= 2:
                        vp_w = int(numbers[0])
                        if vp_w < 800:
                            zoom_level = round(vp_w / 800, 2)
                            zoom_level = max(0.3, min(zoom_level, 1.0))
                            page.evaluate(f"document.body.style.zoom = '{zoom_level}'")
                            print(f"[Cảnh {self.index}] Đã zoom trang web xuống {zoom_level} để hiển thị full.")
                except Exception as e:
                    print(f"[Cảnh {self.index}] Lỗi khi zoom trang: {e}")
                
                # Kiểm tra dừng sau khi goto
                self._check_stop()
                
                # Bước 2: Đợi load xong trang thì Bấm Dự án mới
                try:
                    new_project_btn = page.locator('[data-type="button-overlay"]').first
                    new_project_btn.wait_for(state="visible", timeout=15000)
                    new_project_btn.click()
                    print(f"[Cảnh {self.index}] Đã bấm Dự án mới.")
                    
                    # Chờ xem có tab mới xuất hiện chứa one.google.com không
                    one_google_detected = False
                    for _ in range(6):
                        for p in context.pages:
                            try:
                                if "one.google.com" in p.url:
                                    one_google_detected = True
                                    break
                            except Exception:
                                pass
                        if one_google_detected:
                            break
                        page.wait_for_timeout(500)
                    
                    if one_google_detected:
                        # Kệ tab one.google.com, chờ 2 giây rồi mới quay lại tab Flow gốc
                        print(f"[Cảnh {self.index}] Phát hiện tab one.google.com, chờ 2 giây rồi quay về tab Flow gốc...")
                        page.wait_for_timeout(2000)
                        page.bring_to_front()
                        page.wait_for_timeout(1000)
                        
                        # Bấm lại Dự án mới trên tab Flow gốc
                        new_project_btn = page.locator('[data-type="button-overlay"]').first
                        new_project_btn.wait_for(state="visible", timeout=10000)
                        new_project_btn.click()
                        print(f"[Cảnh {self.index}] Đã bấm Dự án mới (lần 2).")
                        page.wait_for_timeout(1000)
                        
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
                    fs = self.flow_settings
                    print(f"[Cảnh {self.index}] Bắt đầu cấu hình thông số tạo video: {fs}")
                    # Mở menu cài đặt - Nút này có text thay đổi tuỳ theo cấu hình hiện tại (vd: "Nano Banana 2 x2" hoặc "Video 1x")
                    setting_btn = page.locator('button', has_text=re.compile(r'1x|x2|x3|x4|Video|Hình ảnh|Veo|Imagen|Nano', re.IGNORECASE)).last
                    setting_btn.click(timeout=10000)
                    if self._stop_event.wait(1): raise InterruptedError("Đã dừng")
                    
                    # Hàm helper click chính xác text
                    def click_setting(val):
                        if not val: return
                        try:
                            # Pattern cho phép khoảng trắng 2 đầu và không phân biệt hoa thường
                            pattern = re.compile(rf'^\s*{re.escape(str(val).strip())}\s*$', re.IGNORECASE)
                            # Ưu tiên tìm button hoặc thẻ chứa text này
                            locator = page.get_by_text(pattern).last
                            if locator.is_visible(timeout=2000):
                                locator.click(timeout=2000)
                            else:
                                # Fallback nếu get_by_text exact quá khắt khe
                                page.locator(f'button:has-text("{val}"), [role="button"]:has-text("{val}"), span:has-text("{val}")').last.click(timeout=2000)
                        except Exception as e:
                            print(f"[Cảnh {self.index}] ⚠️ Lỗi khi click thiết lập '{val}': {e}")

                    # Chọn loại nội dung (Video / Hình ảnh)
                    click_setting(fs["content_type"])
                    
                    # Chọn loại khung (Khung hình / Thành phần)
                    click_setting(fs["frame_type"])
                    
                    # Chọn tỷ lệ khung hình (9:16 / 16:9)
                    click_setting(fs["aspect_ratio"])
                    
                    # Chọn số lần tạo (1x / x2 / x3 / x4)
                    click_setting(fs["gen_count"])
                    
                    # Chọn mô hình AI (Veo 3.1 - Lite / Veo 3.0 / Veo 2.0)
                    try:
                        pattern = re.compile(rf'^\s*{re.escape(str(fs["ai_model"]).strip())}\s*$', re.IGNORECASE)
                        locator = page.get_by_text(pattern).last
                        if locator.is_visible(timeout=2000):
                            locator.click(timeout=2000)
                        else:
                            # Nếu không thấy mô hình, thử bấm mở dropdown chọn mô hình trước
                            model_btn = page.locator('button:has-text("Veo"), button:has-text("Imagen")').last
                            model_btn.click(timeout=2000)
                            if self._stop_event.wait(0.5): raise InterruptedError("Đã dừng")
                            page.get_by_text(pattern).last.click(timeout=2000)
                    except Exception as e:
                        print(f"[Cảnh {self.index}] ⚠️ Lỗi khi chọn Mô hình AI '{fs['ai_model']}': {e}")
                    
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

                if not self.reference_image_path:
                    raise RuntimeError("Chưa có hình tham chiếu. Vui lòng tạo ảnh tham chiếu trước khi tạo video từng cảnh.")

                self._upload_reference_image_to_flow(page, self.reference_image_path)

                try:
                    search_input = page.get_by_placeholder(re.compile(r"tạo gì|create", re.IGNORECASE)).first
                    search_input.wait_for(state="visible", timeout=5000)
                except:
                    search_input = page.locator('textarea:visible, div[contenteditable="true"]:visible').last

                search_input.wait_for(state="visible", timeout=15000)
                search_input.click(force=True)
                
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
                        
                        # 1. Kiểm tra xem có bảng báo lỗi không (Không thành công / Rất tiếc)
                        try:
                            error_locator = page.locator('text="Không thành công", text="đã xảy ra lỗi", text="Rất tiếc"').first
                            if error_locator.is_visible():
                                print(f"[Cảnh {self.index}] Quá trình tạo kết thúc sớm (Hệ thống báo lỗi).")
                                raise RuntimeError("Hệ thống báo lỗi (Không thành công).")
                        except Exception as e:
                            if isinstance(e, RuntimeError): raise
                            pass

                        if elapsed == 4 and not has_started_generating:
                            print(f"[Cảnh {self.index}] Vẫn chưa thấy bắt đầu tạo, thử click trực tiếp nút Gửi dự phòng...")
                            try:
                                # Thường nút Mũi tên gửi là nút button nằm cuối cùng trên giao diện
                                page.locator('button').last.click(timeout=1500)
                            except:
                                pass
                            
                        # 2. Kiểm tra dấu hiệu đang tạo (thường có chữ 1%, 5%... hoặc progressbar)
                        try:
                            progress_locator = page.locator('text=/^\\d{1,3}%$/').first
                            progressbar_locator = page.get_by_role("progressbar").first
                            queue_locator = page.locator('text="Đang trong hàng đợi", text="In queue"').first
                            
                            is_generating = progress_locator.is_visible() or progressbar_locator.is_visible() or queue_locator.is_visible()
                        except Exception:
                            is_generating = False
                        
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
                    if isinstance(e, RuntimeError) and "Không thành công" in str(e): raise
                    print(f"[Cảnh {self.index}] Lỗi trong vòng lặp chờ kết quả: {e}")
                
                # Bước 6: Sau khi video tạo xong thì tải video về máy
                try:
                    print(f"[Cảnh {self.index}] Đang tiến hành tải video về máy...")
                    
                    # Chuẩn bị tên file và thư mục
                    save_dir = self.save_dir if self.save_dir else os.path.join(os.getcwd(), "videos_da_tao")
                    if not os.path.exists(save_dir):
                        try:
                            os.makedirs(save_dir, exist_ok=True)
                        except Exception:
                            pass
                        
                    # Lọc sạch ký tự không hợp lệ và cả dấu xuống dòng (gây lỗi Errno 22)
                    safe_prompt = re.sub(r'[\\/*?:"<>|\n\r]', " ", self.prompt_text)
                    safe_prompt = safe_prompt[:30].strip() if safe_prompt else "video"
                    filename = f"canh_{self.index}_{safe_prompt}.mp4"
                    save_path = os.path.join(save_dir, filename)

                    # --- CHỤP ẢNH THUMBNAIL TỪ TRÌNH DUYỆT BẰNG PLAYWRIGHT ---
                    thumbnail_path = ""
                    try:
                        thumbnail_filename = f"canh_{self.index}_{safe_prompt}_thumb.png"
                        t_path = os.path.join(save_dir, thumbnail_filename)
                        
                        # Chờ video hiển thị sau khi render xong (thêm chút thời gian cho chắc chắn)
                        page.wait_for_timeout(3000)
                        
                        # Dùng Javascript để tìm tọa độ chính xác của vùng chứa video trên màn hình
                        # (Cách này tránh lỗi is_visible() khắt khe của Playwright khiến nó chụp cả màn hình)
                        box = page.evaluate("""() => {
                            function findVideos(root) {
                                let vids = Array.from(root.querySelectorAll('video'));
                                let els = root.querySelectorAll('*');
                                for (let e of els) {
                                    if (e.shadowRoot) {
                                        vids = vids.concat(findVideos(e.shadowRoot));
                                    }
                                }
                                return vids;
                            }
                            let videos = findVideos(document);
                            for (let i = videos.length - 1; i >= 0; i--) {
                                let v = videos[i];
                                // Nếu thẻ video bị ẩn (width/height = 0), tìm dần lên thẻ cha chứa nó
                                let el = v;
                                while (el && el !== document.body) {
                                    let rect = el.getBoundingClientRect();
                                    // Kiểm tra xem thẻ này có kích thước hiển thị thật không (lớn hơn 100x100)
                                    if (rect.width > 100 && rect.height > 100) {
                                        // Cuộn nó vào giữa màn hình để chụp không bị lỗi
                                        el.scrollIntoView({block: 'center', inline: 'center'});
                                        // Lấy lại tọa độ sau khi cuộn
                                        let finalRect = el.getBoundingClientRect();
                                        return {x: finalRect.x, y: finalRect.y, width: finalRect.width, height: finalRect.height};
                                    }
                                    el = el.parentElement || (el.getRootNode() && el.getRootNode().host);
                                }
                            }
                            return null;
                        }""")
                        
                        # Chờ 1 giây cho thao tác cuộn (nếu có) ổn định
                        page.wait_for_timeout(1000)
                        
                        if box:
                            page.screenshot(path=t_path, clip=box)
                            thumbnail_path = t_path
                            print(f"[Cảnh {self.index}] ✅ Chụp ảnh thumbnail video thành công (tọa độ): {thumbnail_path}")
                        else:
                            # Nếu JS không tìm thấy, thử tìm thẻ hiển thị cuối cùng
                            fallback_el = page.locator('[data-type="video-result"], [role="application"], video').last
                            if fallback_el.is_visible(timeout=2000):
                                fallback_el.screenshot(path=t_path)
                                thumbnail_path = t_path
                                print(f"[Cảnh {self.index}] ✅ Chụp ảnh thumbnail dự phòng thành công: {thumbnail_path}")
                            else:
                                print(f"[Cảnh {self.index}] ⚠️ Không tìm thấy khung video để chụp thumbnail.")
                    except Exception as thumb_err:
                        print(f"[Cảnh {self.index}] ⚠️ Lỗi chụp ảnh thumbnail: {thumb_err}")

                    # --- TẢI VIDEO VỀ MÁY BẰNG JAVASCRIPT / PLAYWRIGHT ---
                    try:
                        print(f"[Cảnh {self.index}] Đang trích xuất dữ liệu video trực tiếp...")
                        
                        video_url = None
                        target_frame = None

                        # CÁCH 1: Dùng Playwright Locator quét qua tất cả frame và xuyên thủng shadow DOM
                        for frame in page.frames:
                            try:
                                vids = frame.locator('video').all()
                                if vids:
                                    for vid in reversed(vids):
                                        url = vid.evaluate("el => el.src || (el.querySelector('source') ? el.querySelector('source').src : '')")
                                        if url:
                                            video_url = url
                                            target_frame = frame
                                            break
                            except Exception:
                                pass
                            if video_url: break

                        # CÁCH 2: Dùng JS đệ quy xuyên Shadow DOM trên từng frame (Dự phòng)
                        if not video_url:
                            for frame in page.frames:
                                try:
                                    url = frame.evaluate("""
                                        () => {
                                            function findVideos(root) {
                                                let vids = Array.from(root.querySelectorAll('video'));
                                                let els = root.querySelectorAll('*');
                                                for (let e of els) {
                                                    if (e.shadowRoot) vids = vids.concat(findVideos(e.shadowRoot));
                                                }
                                                return vids;
                                            }
                                            let videos = findVideos(document);
                                            for (let i = videos.length - 1; i >= 0; i--) {
                                                let v = videos[i];
                                                let tempUrl = v.src || (v.querySelector('source') ? v.querySelector('source').src : null);
                                                if (tempUrl) return tempUrl;
                                            }
                                            return null;
                                        }
                                    """)
                                    if url:
                                        video_url = url
                                        target_frame = frame
                                        break
                                except Exception:
                                    pass

                        if not video_url:
                            raise Exception("Không tìm thấy thẻ video hoặc URL video nào hợp lệ trên toàn bộ trang (kể cả trong iframe).")
                        
                        print(f"[Cảnh {self.index}] Đã lấy được URL video: {video_url[:80]}...")

                        if video_url.startswith('blob:'):
                            if not target_frame: target_frame = page
                            # Dùng JS fetch cho blob URL bên trong đúng frame chứa nó
                            base64_data = target_frame.evaluate(f"""
                                async () => {{
                                    let response = await fetch("{video_url}");
                                    let blob = await response.blob();
                                    return new Promise((resolve, reject) => {{
                                        let reader = new FileReader();
                                        reader.onloadend = () => resolve(reader.result);
                                        reader.onerror = reject;
                                        reader.readAsDataURL(blob);
                                    }});
                                }}
                            """)
                            if base64_data and "," in base64_data:
                                import base64
                                header, encoded = base64_data.split(",", 1)
                                with open(save_path, "wb") as f:
                                    f.write(base64.b64decode(encoded))
                            else:
                                raise Exception("Dữ liệu base64 trả về không hợp lệ")
                        else:
                            # Dùng Playwright Context Request API để tải http/https (bỏ qua CORS, kèm cookie)
                            resp = context.request.get(video_url)
                            if resp.ok:
                                with open(save_path, "wb") as f:
                                    f.write(resp.body())
                            else:
                                raise Exception(f"Lỗi tải video HTTP {resp.status} - {resp.status_text}")
                            
                        print(f"[Cảnh {self.index}] ✅ Đã tải video thành công: {save_path}")
                        
                        emit_data = f"{save_path}|{thumbnail_path}" if thumbnail_path else save_path
                        self.record.emit(self.index, "Tải video thành công", emit_data)
                    except Exception as js_err:
                        print(f"[Cảnh {self.index}] ❌ Lỗi khi tải video: {js_err}")
                        raise RuntimeError(f"Lỗi tải video: {js_err}")

                except Exception as e:
                    print(f"[Cảnh {self.index}] ❌ Lỗi tải video chung: {e}")

                # Bước 7: Đợi thêm 55 giây rồi mới kết thúc quy trình
                print(f"[Cảnh {self.index}] Đang đợi thêm 55 giây và liên tục cập nhật ảnh thumbnail...")
                for _ in range(11):  # 11 lần x 5s = 55s
                    if self._stop_event.wait(5): raise InterruptedError("Đã dừng")
                    
                    try:
                        # Thử chụp lại ảnh thumbnail mới nhất (do đôi khi video load chậm)
                        box = page.evaluate("""() => {
                            function findVideos(root) {
                                let vids = Array.from(root.querySelectorAll('video'));
                                let els = root.querySelectorAll('*');
                                for (let e of els) {
                                    if (e.shadowRoot) {
                                        vids = vids.concat(findVideos(e.shadowRoot));
                                    }
                                }
                                return vids;
                            }
                            let videos = findVideos(document);
                            for (let i = videos.length - 1; i >= 0; i--) {
                                let v = videos[i];
                                let el = v;
                                while (el && el !== document.body) {
                                    let rect = el.getBoundingClientRect();
                                    if (rect.width > 100 && rect.height > 100) {
                                        return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
                                    }
                                    el = el.parentElement || (el.getRootNode() && el.getRootNode().host);
                                }
                            }
                            return null;
                        }""")
                        
                        if box and 'thumbnail_path' in locals() and thumbnail_path:
                            page.screenshot(path=thumbnail_path, clip=box)
                            # Bắn tín hiệu để UI load lại ảnh mới (gửi đè đường dẫn cũ)
                            if 'save_path' in locals() and save_path:
                                emit_data = f"{save_path}|{thumbnail_path}"
                                self.record.emit(self.index, "Tải video thành công", emit_data)
                    except Exception:
                        pass
                
                # Sau khi xong, đánh dấu hoàn thành và đóng browser
                status = "Hoàn thành"
                print(f"[Cảnh {self.index}] ✅ Đã gõ prompt xong và đợi 55s, chuẩn bị đóng browser")
                browser.close()
                
        except InterruptedError:
            print(f"[Cảnh {self.index}] Luồng đã bị ngắt bởi người dùng.")
            status = "Đã dừng"
            response_data = "-"
        except PlaywrightError as e:
            if "TargetClosedError" in str(e.__class__) or "has been closed" in str(e):
                print(f"[Cảnh {self.index}] Trình duyệt đã bị đóng (hoặc do người dùng dừng).")
                status = "Đã dừng"
                response_data = "-"
            else:
                import traceback
                print(f"[Cảnh {self.index}] Lỗi Playwright:\n{traceback.format_exc()}")
                status = f"Lỗi: {e}"
                response_data = "-"
        except Exception as e:
            if "TargetClosedError" in str(e.__class__) or "has been closed" in str(e):
                print(f"[Cảnh {self.index}] Trình duyệt đã bị đóng (hoặc do người dùng dừng).")
                status = "Đã dừng"
                response_data = "-"
            else:
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
                    # GPM trả về False + message OK thường là profile đã đóng
                    if response.get("message") in ["OK", "Profile is not running", ""]:
                        print(f"[Cảnh {self.index}] ✅ Profile {profile_id} đã được đóng trước đó.")
                        close_success = True
                        break
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
        
        # Chủ động gọi close_profile để ngắt các hàm block của Playwright (vd: wait_for)
        try:
            gpm = Gpm()
            gpm.close_profile(apiurl_Gpm=self.api_url_gpm, id_profile=self.profile_id)
        except:
            pass

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

class ConcatVideoThread(QThread):
    """Thread ghép nối tất cả video cảnh thành 1 video duy nhất bằng moviepy."""
    finished = pyqtSignal(bool, str)  # (thành công, đường dẫn hoặc lỗi)

    def __init__(self, video_files, output_path):
        super().__init__()
        self.video_files = video_files
        self.output_path = output_path

    def run(self):
        clips = []
        final_clip = None
        try:
            from moviepy.video.io.VideoFileClip import VideoFileClip
            from moviepy.video.compositing.CompositeVideoClip import concatenate_videoclips

            print(f"[Ghép video] Đang load {len(self.video_files)} video cảnh...")
            for vf in self.video_files:
                print(f"  - Đang load: {os.path.basename(vf)}")
                clip = VideoFileClip(vf)
                clips.append(clip)

            if not clips:
                self.finished.emit(False, "Không có video nào để ghép.")
                return

            print(f"[Ghép video] Đang ghép nối {len(clips)} video...")
            final_clip = concatenate_videoclips(clips, method="compose")

            print(f"[Ghép video] Đang xuất file: {self.output_path}")
            final_clip.write_videofile(
                self.output_path,
                codec="libx264",
                audio_codec="aac",
                logger=None  # Tắt log moviepy để không spam console
            )

            if os.path.exists(self.output_path):
                print(f"[Ghép video] ✅ Thành công: {self.output_path}")
                self.finished.emit(True, self.output_path)
            else:
                self.finished.emit(False, "File xuất ra không tồn tại sau khi ghép.")
        except ImportError as e:
            self.finished.emit(False, f"Thiếu thư viện ghép video trong bản build.\n\nChi tiết: {e}")
        except Exception as e:
            import traceback
            print(f"[Ghép video] ❌ Lỗi: {traceback.format_exc()}")
            self.finished.emit(False, f"Lỗi ghép video: {e}")
        finally:
            if final_clip is not None:
                try:
                    final_clip.close()
                except Exception:
                    pass
            for clip in clips:
                try:
                    clip.close()
                except Exception:
                    pass



class RefImageThread(QThread):
    # Signal: (success, image_path_or_error_msg)
    finished = pyqtSignal(bool, str)

    def __init__(self, api_url_gpm, profile_id, prompt_text, win_size, win_pos, save_dir):
        super().__init__()
        self.api_url_gpm = api_url_gpm
        self.profile_id = profile_id
        self.prompt_text = prompt_text
        self.win_size = win_size
        self.win_pos = win_pos
        self.save_dir = save_dir
        self.is_running = True
        self._stop_event = threading.Event()

    def run(self):
        gpm = Gpm()
        profile_id = self.profile_id
        status_msg = ""
        success = False
        image_path = ""
        
        try:
            if not self.is_running:
                return
            if not profile_id:
                raise RuntimeError("Chưa có ID Profile.")

            remote_addr = None
            for open_attempt in range(2):
                remote_addr = gpm.open_profile(apiurl_Gpm=self.api_url_gpm, id_profile=profile_id, win_pos=self.win_pos, win_size=self.win_size)
                if remote_addr:
                    break
                print(f"[Ảnh Tham Chiếu] Không mở được Profile GPM lần {open_attempt + 1}, thử đóng profile rồi mở lại...")
                try:
                    gpm.close_profile(apiurl_Gpm=self.api_url_gpm, id_profile=profile_id)
                except Exception:
                    pass
                if self._stop_event.wait(2):
                    raise InterruptedError("Đã dừng")
            if not remote_addr:
                raise RuntimeError("Không thể mở Profile GPM. Hãy kiểm tra GPM Global đang mở, API URL đúng và ID Profile tồn tại.")

            if not self.is_running: return

            with sync_playwright() as p:
                browser = None
                for attempt in range(5):
                    try:
                        browser = p.chromium.connect_over_cdp(f"http://{remote_addr}")
                        break
                    except Exception:
                        print(f"[Ảnh Tham Chiếu] Lỗi kết nối CDP (lần {attempt+1}), thử lại sau 2s...")
                        if self._stop_event.wait(2):
                            raise InterruptedError("Đã dừng")
                if not browser:
                    raise RuntimeError("Không thể kết nối Playwright.")
                
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                
                try:
                    numbers = re.findall(r'\d+', str(self.win_size))
                    if len(numbers) >= 2:
                        w, h = int(numbers[0]), int(numbers[1])
                        # Trừ 85px = chiều cao thanh công cụ trình duyệt (Address bar + Title bar)
                        # Viewport khớp đúng kích thước cửa sổ thực tế để hiển thị full
                        vp_w = w
                        vp_h = max(300, h - 85)
                        page.set_viewport_size({"width": vp_w, "height": vp_h})
                except:
                    pass

                try:
                    page.goto("https://labs.google/fx/vi/tools/flow", timeout=60000, wait_until="domcontentloaded")
                except PlaywrightTimeoutError:
                    pass

                # Tự động zoom trang web để nội dung hiển thị gần full trong viewport nhỏ
                try:
                    numbers = re.findall(r'\d+', str(self.win_size))
                    if len(numbers) >= 2:
                        vp_w = int(numbers[0])
                        if vp_w < 800:
                            zoom_level = round(vp_w / 800, 2)
                            zoom_level = max(0.3, min(zoom_level, 1.0))
                            page.evaluate(f"document.body.style.zoom = '{zoom_level}'")
                            print(f"[Ảnh Tham Chiếu] Đã zoom trang web xuống {zoom_level} để hiển thị full.")
                except Exception as e:
                    print(f"[Ảnh Tham Chiếu] Lỗi khi zoom trang: {e}")

                self._check_stop()

                # Bấm Dự án mới
                try:
                    new_project_btn = page.locator('[data-type="button-overlay"]').first
                    new_project_btn.wait_for(state="visible", timeout=15000)
                    new_project_btn.click()
                    
                    one_google_detected = False
                    for _ in range(6):
                        for px in context.pages:
                            try:
                                if "one.google.com" in px.url:
                                    one_google_detected = True
                                    break
                            except:
                                pass
                        if one_google_detected: break
                        page.wait_for_timeout(500)
                    
                    if one_google_detected:
                        page.wait_for_timeout(2000)
                        page.bring_to_front()
                        page.wait_for_timeout(1000)
                        new_project_btn = page.locator('[data-type="button-overlay"]').first
                        new_project_btn.wait_for(state="visible", timeout=10000)
                        new_project_btn.click()
                        page.wait_for_timeout(1000)
                except Exception as e:
                    print("Lỗi bấm Dự án mới:", e)

                # Popup "Bắt đầu" và Cookie
                try:
                    page.locator('button:has-text("Bắt đầu")').last.click(timeout=3000)
                except: pass
                try:
                    page.locator('button[aria-label="Close"], button[aria-label="Đóng"], [role="dialog"] button:has(svg)').first.click(timeout=3000)
                except: pass

                self._check_stop()

                # Cấu hình "Hình ảnh", "16:9", "1x", "Nano Banana Pro"
                try:
                    setting_btn = page.locator('button', has_text=re.compile(r'1x|x2|x3|x4|Video|Hình ảnh|Veo|Imagen|Nano', re.IGNORECASE)).last
                    setting_btn.click(timeout=10000)
                    
                    def click_setting(val):
                        try:
                            pattern = re.compile(rf'^\s*{re.escape(str(val).strip())}\s*$', re.IGNORECASE)
                            locator = page.get_by_text(pattern).last
                            if locator.is_visible(timeout=2000):
                                locator.click(timeout=2000)
                            else:
                                page.locator(f'button:has-text("{val}"), [role="button"]:has-text("{val}"), span:has-text("{val}")').last.click(timeout=2000)
                        except: pass

                    click_setting("Hình ảnh")
                    click_setting("16:9")
                    click_setting("1x")
                    
                    try:
                        # BƯỚC 1: Kích vào phần khoanh đỏ (Mở settings panel)
                        print("[Ảnh Tham Chiếu] Bước 1: Kích vào phần khoanh đỏ (Mở panel)...")
                        # Tìm nút dưới cùng có chứa tên model và tỉ lệ '1x'
                        red_chip = page.locator('button').filter(has_text=re.compile(r'Nano|Veo|Imagen', re.IGNORECASE)).filter(has_text=re.compile(r'1x|16:9|crop', re.IGNORECASE)).last
                        red_chip.click(force=True, timeout=5000)
                        page.wait_for_timeout(2000)

                        # BƯỚC 2: Kích vào phần mũi tên khoanh vàng (Mở dropdown model)
                        print("[Ảnh Tham Chiếu] Bước 2: Kích vào mũi tên khoanh vàng (Mở dropdown)...")
                        # Tìm nút có aria-haspopup="menu" (đặc trưng của nút dropdown) và chứa tên model
                        yellow_btn = page.locator('button[aria-haspopup="menu"]').filter(has_text=re.compile(r'Nano|Veo|Imagen', re.IGNORECASE)).first
                        
                        # Ưu tiên click thẳng vào thẻ <i> chứa mũi tên bên trong để chắc chắn nhất
                        arrow_icon = yellow_btn.locator('i').filter(has_text='arrow_drop_down')
                        if arrow_icon.count() > 0:
                            arrow_icon.first.click(force=True, timeout=5000)
                        else:
                            yellow_btn.click(force=True, timeout=5000)
                        
                        page.wait_for_timeout(2000)

                        # BƯỚC 3: Chọn option trên cùng (Mô hình mới nhất)
                        print("[Ảnh Tham Chiếu] Bước 3: Chọn option trên cùng (Mô hình mới nhất)...")
                        # Lấy popup menu nằm cuối cùng vừa được mở ra
                        last_menu = page.locator('[role="menu"], [role="listbox"], [data-radix-menu-content]').last
                        # Lấy option đầu tiên bên trong nó
                        top_option = last_menu.locator('[role="menuitem"], [role="option"], li, button').first
                        
                        top_option.click(force=True, timeout=5000)
                        page.wait_for_timeout(2000)
                        
                        print("[Ảnh Tham Chiếu] ✅ Đã hoàn thành chọn model mới nhất thành công!")

                    except Exception as e_model:
                        print(f"[Ảnh Tham Chiếu] ⚠️ Lỗi khi chọn model (theo Playwright Locator): {e_model}")
                except: pass
                finally:
                    page.keyboard.press("Escape")
                
                self._check_stop()

                # Gõ prompt
                print(f"[Ảnh Tham Chiếu] Bắt đầu nhập prompt: {self.prompt_text[:30]}...")
                
                # Đảm bảo panel đã đóng bằng cách bấm Escape và click ra ngoài màn hình
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
                page.mouse.click(10, 10)
                page.wait_for_timeout(500)

                try:
                    search_input = page.get_by_placeholder(re.compile(r"tạo gì|create|tạo", re.IGNORECASE)).first
                    search_input.wait_for(state="visible", timeout=5000)
                except:
                    search_input = page.locator('textarea:visible, div[contenteditable="true"]:visible').last
                
                search_input.wait_for(state="visible", timeout=15000)
                # Cuộn input vào giữa vùng nhìn thấy để đảm bảo hiển thị đầy đủ
                try:
                    search_input.scroll_into_view_if_needed(timeout=3000)
                    page.wait_for_timeout(300)
                except:
                    pass
                
                # Tập trung vào ô input
                search_input.click(force=True)
                page.wait_for_timeout(500)
                
                try: search_input.clear()
                except: pass
                
                # Điền thẳng nguyên khối prompt 1 lần duy nhất (như copy-paste)
                page.keyboard.insert_text(self.prompt_text)
                
                # Đợi điền hết prompt xong mới bấm gửi
                page.wait_for_timeout(1500)
                print("[Ảnh Tham Chiếu] Đã điền xong prompt 1 lần. Đang bấm Gửi...")
                
                # Ưu tiên click nút Gửi (Mũi tên phải) kế bên ô nhập prompt nếu có
                try:
                    # Dùng JS tìm nút chứa svg mũi tên ở gần cuối trang
                    page.evaluate("""
                        () => {
                            let btns = document.querySelectorAll('button');
                            for (let i = btns.length - 1; i >= Math.max(0, btns.length - 5); i--) {
                                if (btns[i].querySelector('svg')) {
                                    btns[i].click();
                                    return;
                                }
                            }
                        }
                    """)
                except: pass
                
                page.wait_for_timeout(500)
                # Dự phòng bấm phím Enter
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)

                # Chờ sinh ảnh
                print("[Ảnh Tham Chiếu] Đang đợi sinh ảnh...")
                timeout = REFERENCE_IMAGE_GENERATION_MAX_TIMEOUT
                elapsed = 0
                has_started = False
                disappear_count = 0
                
                while elapsed < timeout:
                    self._check_stop()
                    if elapsed == 4 and not has_started:
                        try: page.locator('button').last.click(timeout=1500)
                        except: pass

                    try:
                        error_loc = page.locator('text="Không thành công", text="đã xảy ra lỗi", text="Rất tiếc"').first
                        if error_loc.is_visible():
                            raise RuntimeError("Hệ thống báo lỗi khi sinh ảnh.")
                    except RuntimeError:
                        raise
                    except Exception:
                        pass

                    try:
                        is_generating = page.locator(r'text=/^\d{1,3}%$/').first.is_visible() or page.get_by_role("progressbar").first.is_visible() or page.locator('text="Đang trong hàng đợi", text="In queue"').first.is_visible()
                    except Exception:
                        is_generating = False
                    
                    if is_generating:
                        has_started = True
                        disappear_count = 0
                    else:
                        if has_started:
                            disappear_count += 2
                            if disappear_count >= 10:
                                break
                        else:
                            if elapsed > REFERENCE_IMAGE_GENERATION_START_TIMEOUT:
                                raise RuntimeError(f"Timeout: Không thấy tiến trình tạo ảnh sau {REFERENCE_IMAGE_GENERATION_START_TIMEOUT}s.")
                    
                    if self._stop_event.wait(2): raise InterruptedError("Đã dừng")
                    elapsed += 2

                # Lấy kết quả
                print("[Ảnh Tham Chiếu] Bắt đầu tải ảnh...")
                page.wait_for_timeout(3000)
                
                # Đảm bảo thư mục lưu tồn tại (exist_ok=True để không lỗi nếu đã có)
                os.makedirs(self.save_dir, exist_ok=True)
                
                # Lọc tên file an toàn: CHỈ giữ chữ cái, số, khoảng trắng, gạch ngang, gạch dưới
                # Loại bỏ toàn bộ ký tự đặc biệt (', [, ], /, \, :, ?, *, <, >, |, ", dấu ngoặc...)
                safe_prompt = re.sub(r'[^\w\s-]', '', self.prompt_text, flags=re.ASCII)
                safe_prompt = re.sub(r'\s+', '_', safe_prompt).strip('_')  # Thay khoảng trắng bằng _
                safe_prompt = safe_prompt[:30].strip('_') or "ref"
                filename = f"ref_image_{safe_prompt}_{int(time.time())}.png"
                image_path = os.path.join(self.save_dir, filename)
                print(f"[Ảnh Tham Chiếu] Đường dẫn lưu ảnh: {image_path}")

                # Tạm tắt CSS zoom trước khi tìm ảnh và chụp screenshot
                # (CSS zoom làm lệch tọa độ getBoundingClientRect)
                try:
                    page.evaluate("document.body.style.zoom = '1.0'")
                except:
                    pass

                # Thử lấy ảnh qua JS (hỗ trợ cả blob: và https:// URL)
                base64_data = None
                try:
                    base64_data = page.evaluate('''
                        async () => {
                            let imgs = document.querySelectorAll('img');
                            let url = null;
                            let largestSize = 0;
                            // Tìm ảnh lớn nhất trên màn hình (ưu tiên blob: trước)
                            for (let i of imgs) {
                                if (i.src && i.src.startsWith('blob:')) {
                                    let rect = i.getBoundingClientRect();
                                    let size = rect.width * rect.height;
                                    if (size > largestSize) {
                                        largestSize = size;
                                        url = i.src;
                                    }
                                }
                            }
                            // Nếu không có blob:, thử tìm ảnh https:// lớn nhất
                            if (!url) {
                                largestSize = 0;
                                for (let i of imgs) {
                                    if (i.src && (i.src.startsWith('https://') || i.src.startsWith('http://'))) {
                                        let rect = i.getBoundingClientRect();
                                        let size = rect.width * rect.height;
                                        if (size > largestSize && size > 10000) {
                                            largestSize = size;
                                            url = i.src;
                                        }
                                    }
                                }
                            }
                            // Fallback: tìm trong node kết quả ảnh
                            if (!url) {
                                let resNode = document.querySelector('[data-type="image-result"], [class*="result"] img, [class*="output"] img');
                                if (resNode) {
                                    let imgEl = resNode.tagName === 'IMG' ? resNode : resNode.querySelector('img');
                                    if (imgEl && imgEl.src) url = imgEl.src;
                                }
                            }
                            if (!url) throw new Error("Không tìm thấy ảnh kết quả");
                            let response = await fetch(url);
                            if (!response.ok) throw new Error("Fetch ảnh thất bại: " + response.status);
                            let blob = await response.blob();
                            return new Promise((resolve, reject) => {
                                let reader = new FileReader();
                                reader.onloadend = () => resolve(reader.result);
                                reader.onerror = reject;
                                reader.readAsDataURL(blob);
                            });
                        }
                    ''')
                except Exception as js_fetch_err:
                    print(f"[Ảnh Tham Chiếu] ⚠️ JS fetch thất bại: {js_fetch_err}, thử chụp screenshot vùng ảnh...")
                    base64_data = None

                if base64_data and "," in base64_data:
                    header, encoded = base64_data.split(",", 1)
                    with open(image_path, "wb") as f:
                        f.write(base64.b64decode(encoded))
                    success = True
                    status_msg = image_path
                else:
                    # Fallback: chụp screenshot vùng chứa ảnh nếu JS fetch không được
                    try:
                        box = page.evaluate("""() => {
                            let imgs = document.querySelectorAll('img');
                            let largestEl = null;
                            let largestSize = 0;
                            for (let i of imgs) {
                                if (!i.src) continue;
                                let rect = i.getBoundingClientRect();
                                let size = rect.width * rect.height;
                                if (size > largestSize && size > 5000) {
                                    largestSize = size;
                                    largestEl = i;
                                }
                            }
                            if (largestEl) {
                                largestEl.scrollIntoView({block: 'center', inline: 'center'});
                                let r = largestEl.getBoundingClientRect();
                                return {x: r.x, y: r.y, width: r.width, height: r.height};
                            }
                            return null;
                        }""")
                        page.wait_for_timeout(800)
                        if box and box.get('width', 0) > 50 and box.get('height', 0) > 50:
                            page.screenshot(path=image_path, clip=box)
                            success = True
                            status_msg = image_path
                            print(f"[Ảnh Tham Chiếu] ✅ Chụp screenshot vùng ảnh thành công: {image_path}")
                        else:
                            raise Exception("Không tìm thấy vùng ảnh để chụp.")
                    except Exception as ss_err:
                        raise Exception(f"Lỗi khi tải ảnh: {ss_err}")
                
                browser.close()
                
        except Exception as e:
            success = False
            status_msg = str(e)
            print("[Ảnh Tham Chiếu] Lỗi:", e)
        finally:
            self.finished.emit(success, status_msg)
            try:
                gpm.close_profile(apiurl_Gpm=self.api_url_gpm, id_profile=profile_id)
            except: pass

    def stop(self):
        self.is_running = False
        self._stop_event.set()
        try:
            gpm = Gpm()
            gpm.close_profile(apiurl_Gpm=self.api_url_gpm, id_profile=self.profile_id)
        except: pass

    def _check_stop(self):
        if not self.is_running:
            raise InterruptedError("Đã dừng")


class Manager(QtWidgets.QMainWindow, Ui_Widget):
    ref_image_signal = pyqtSignal(bool, object)
    ref_image_webhook_signal = pyqtSignal(bool, object)

    def __init__(self):
        super().__init__()
        self.setupUi(self)

        self.config = {}  # Dictionary để lưu config values không phải widget
        # Tìm config.env từ thư mục _internal khi chạy EXE, hoặc thư mục chứa .py script
        if getattr(sys, 'frozen', False):
            # Chạy từ EXE (PyInstaller) - tìm trong _internal hoặc cùng thư mục exe
            exe_dir = os.path.dirname(sys.executable)
            internal_dir = os.path.join(exe_dir, '_internal')
            if os.path.exists(os.path.join(internal_dir, 'config.env')):
                base_path = internal_dir
            else:
                base_path = exe_dir
        else:
            # Chạy từ Python source
            base_path = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(base_path, "config.env")
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
            "FLOW_CONTENT_TYPE": self.cb_flow_content_type,
            "FLOW_FRAME_TYPE": self.cb_flow_frame_type,
            "FLOW_ASPECT_RATIO": self.cb_flow_aspect_ratio,
            "FLOW_GEN_COUNT": self.cb_flow_gen_count,
            "FLOW_AI_MODEL": self.cb_flow_ai_model,
        }

        self._load_config()
        self._config_map["KIE_AI_API_KEY"] = self.kie_le_api_key
        self._config_map["KIE_LANG"] = self.kie_cb_lang
        self._config_map["KIE_LINK"] = self.kie_le_link
        self._config_map["KIE_DESC"] = self.kie_le_desc
        self.scene_prompt_boxes = self.tab_veo3.findChildren(QtWidgets.QTextEdit, "promptBox")
        self.scene_preview_containers = self.tab_veo3.findChildren(QtWidgets.QStackedWidget, "previewContainer")
        self._active_run_prompt_boxes = self.scene_prompt_boxes
        self._connect_config_signals()

        self.threads = []
        self.completed_threads = 0
        self.total_threads = 0
        self.running_threads = 0    # Số luồng đang chạy thực tế
        self._is_stopping = False   # Flag để bỏ qua signal của thread khi đã bấm dừng
        self.reference_image_path = ""

        # Kết nối nút proxy: mở rộng / thu gọn bảng nhập proxy
        self.btn_proxy_collapsed.clicked.connect(self._toggle_proxy_panel)
        self.btn_proxy_close.clicked.connect(self._toggle_proxy_panel)

        self.prompt_thread = None
        
        # Thêm timer và biến cho hiệu ứng "Đang xử lý"
        self.loading_timer = QtCore.QTimer(self)
        self.loading_timer.timeout.connect(self._animate_loading_button)
        self.dot_count = 0
        self.is_prompt_running = False
        self.is_concat_running = False
        self.prompt_scene_count = 0

        # Kết nối sự kiện Click cho nút "BẮT ĐẦU TẠO VIDEO" bên tab Veo3 và Kie AI
        self.veo3_btn_analyze.clicked.connect(self.startThreadVeo3)
        if hasattr(self, "kie_btn_analyze"):
            self.kie_btn_analyze.clicked.connect(self.startThreadVeo3)
        
        # Kết nối sự kiện Click cho nút "Bắt đầu phân tích tạo Prompt" cho cả 2 tab
        self.veo3_btn_merge.clicked.connect(self.analyzePrompts)
        self.kie_btn_merge.clicked.connect(self.analyzePrompts)
        
        # Kết nối nút cập nhật phiên bản cho cả hai tab
        self.veo3_btn_update.clicked.connect(self._update_version)
        self.kie_btn_update.clicked.connect(self._update_version)

        # Kết nối nút ghép video cho cả hai tab
        self.concat_thread = None
        self.veo3_btn_concat.clicked.connect(self.concatAllScenes)
        self.kie_btn_concat.clicked.connect(self.concatAllScenes)

        # Kết nối nút tạo ảnh tham chiếu nhân vật
        if hasattr(self, 'veo3_btn_create_ref'):
            self.veo3_btn_create_ref.clicked.connect(self.createReferenceImage)
            self.ref_image_signal.connect(self._on_ref_image_result)
            self.ref_image_webhook_signal.connect(self._on_ref_image_webhook_result)

        # Kết nối nút "Mở thư mục xuất này" → mở folder chứa video/ảnh
        if hasattr(self, 'btn_open_folder'):
            self.btn_open_folder.clicked.connect(self._open_output_folder)

        # Kết nối logic tự động Kie AI từ file riêng (kie_ai_auto.py)
        setup_kie_ai_connections(self)

    def _animate_loading_button(self):
        self.dot_count = (self.dot_count + 1) % 4
        dots = "." * self.dot_count
        
        if self.is_prompt_running:
            text = f"⏱ Đang xử lý {self.prompt_scene_count} cảnh{dots}"
            self.veo3_btn_running.setText(text)
            self.kie_btn_running.setText(text)
        elif self.running_threads > 0:
            text = f"⏱ Đang xử lý {self.running_threads} cảnh{dots}"
            self.veo3_btn_running.setText(text)
            self.kie_btn_running.setText(text)

        if getattr(self, "is_concat_running", False):
            text = f"⏳ Đang ghép video vui lòng chờ{dots}"
            self.veo3_btn_concat.setText(text)
            self.kie_btn_concat.setText(text)

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
            self.kie_btn_running.setStyleSheet(style)

    def _stop_btn_running_animation(self):
        if not self.is_prompt_running and self.running_threads == 0 and not getattr(self, "is_concat_running", False):
            self.loading_timer.stop()
            self.veo3_btn_running.setStyleSheet("")
            self.kie_btn_running.setStyleSheet("")
            self.veo3_btn_running.setText("⏱ Đang xử lý 0 cảnh")
            self.kie_btn_running.setText("⏱ Đang xử lý 0 cảnh")

    def _start_concat_animation(self):
        self.is_concat_running = True
        if not self.loading_timer.isActive():
            self.dot_count = 0
            self.loading_timer.start(500)
        # Đổi màu nút sang cam giống nút chạy
        style = (
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #d97706, stop:1 #b45309);"
            "border: 1px solid #d97706;"
            "color: white; font-weight: bold; border-radius: 6px;"
        )
        self.veo3_btn_concat.setStyleSheet(style)
        self.kie_btn_concat.setStyleSheet(style)

    def _stop_concat_animation(self):
        self.is_concat_running = False
        if not self.is_prompt_running and self.running_threads == 0:
            self.loading_timer.stop()
        self.veo3_btn_concat.setStyleSheet("")
        self.kie_btn_concat.setStyleSheet("")

    def _set_veo3_btn_running(self, is_running):
        """Đổi trạng thái nút BẮT ĐẦU TẠO VIDEO theo trạng thái luồng."""
        if is_running:
            text = "⏹  ĐANG CHẠY TẠO VIDEO...BẤM ĐỂ DỪNG CHẠY"
            style = (
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                " stop:0 #b91c1c, stop:1 #ef4444);"
                " border: none; color: white; font-weight: bold;"
            )
            self.veo3_btn_analyze.setText(text)
            self.veo3_btn_analyze.setStyleSheet(style)
            if hasattr(self, "kie_btn_analyze"):
                self.kie_btn_analyze.setText(text)
                self.kie_btn_analyze.setStyleSheet(style)
            self._start_btn_running_animation()
        else:
            text = "🚀  Bắt đầu tạo video từ tất cả cảnh"
            self.veo3_btn_analyze.setText(text)
            # Xoá style inline → fallback về QSS gốc (#analyzeBtn)
            self.veo3_btn_analyze.setStyleSheet("")
            if hasattr(self, "kie_btn_analyze"):
                self.kie_btn_analyze.setText(text)
                self.kie_btn_analyze.setStyleSheet("")
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
            self.kie_btn_merge.setText(text)
            self.kie_btn_merge.setStyleSheet(style)
            self._start_btn_running_animation()
        else:
            self.is_prompt_running = False
            text = "🎬 Bắt đầu phân tích tạo Prompt"
            self.veo3_btn_merge.setText(text)
            self.veo3_btn_merge.setStyleSheet("")
            self.kie_btn_merge.setText(text)
            self.kie_btn_merge.setStyleSheet("")
            self._stop_btn_running_animation()

    def analyzePrompts(self):
        try:
            input_soluong = int(self.cb_scene_count.currentText())
        except ValueError:
            QMessageBox.warning(self, "Lỗi", "Số lượng cảnh không hợp lệ.")
            return

        if input_soluong <= 0:
            return
        if input_soluong > 10:
            QMessageBox.warning(self, "Lỗi", "Số lượng cảnh tối đa là 10.")
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
                target_tab = self.tab_kie_ai
                
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

    def _get_active_scene_prompt_boxes(self):
        target_tab = self.tab_veo3 if self.tabWidget.currentIndex() == 0 else self.tab_kie_ai
        return target_tab.findChildren(QtWidgets.QTextEdit, "promptBox")

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

        raw_reference_image_path = str(getattr(self, "reference_image_path", "") or "").strip()
        reference_image_path = os.path.abspath(raw_reference_image_path) if raw_reference_image_path else ""
        if not reference_image_path or not os.path.exists(reference_image_path):
            QMessageBox.warning(
                self,
                "Thiếu ảnh tham chiếu",
                "Vui lòng bấm 'Bấm để tạo hình tham chiếu nhân vật' và đợi ảnh hiển thị ở khung ẢNH THAM CHIẾU trước khi tạo video từng cảnh."
            )
            return

        # Đọc danh sách Profile ID từ ô nhập thủ công trên giao diện
        # Giữ nguyên vị trí từng dòng: luồng i lấy ID dòng i
        profile_id_list = []
        proxy_text = self.te_proxy_input.toPlainText().strip()
        if proxy_text:
            for line in proxy_text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
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
        self._active_run_prompt_boxes = self._get_active_scene_prompt_boxes()
        
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
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            screen_rect = screen.availableGeometry()
        else:
            screen_rect = QtCore.QRect(0, 0, 1920, 1080)
        screen_width = screen_rect.width()
        cols = max(1, screen_width // win_w)

        # Khởi tạo GPM theo đúng số "cảnh" đã chọn
        for i in range(1, input_soluong + 1):
            if self._is_stopping:
                break

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
            if (i - 1) < len(self._active_run_prompt_boxes):
                text = self._active_run_prompt_boxes[i - 1].toPlainText().strip()
                if text:
                    current_prompt = text
                    
            # Lấy thông số cấu hình Veo3 Flow từ giao diện
            flow_settings = {
                "content_type": self.cb_flow_content_type.currentText(),
                "frame_type": self.cb_flow_frame_type.currentText(),
                "aspect_ratio": self.cb_flow_aspect_ratio.currentText(),
                "gen_count": self.cb_flow_gen_count.currentText(),
                "ai_model": self.cb_flow_ai_model.currentText(),
            }

            save_dir = self.le_folder.text().strip() if hasattr(self, 'le_folder') else ""

            thread = MultiThread(
                index=i,
                api_url_gpm=input_api_url_gpm,
                profile_id=profile_id,
                prompt_text=current_prompt,
                win_size=input_win_size,
                win_pos=win_pos,
                flow_settings=flow_settings,
                save_dir=save_dir,
                reference_image_path=reference_image_path
            )

            # Lắng nghe dữ liệu bắn về từ Thread để update log
            thread.record.connect(self.update_data)
            self.threads.append(thread)
            thread.start()
            
            # Delay 1.5 giây giữa các luồng để tránh spam API GPM và treo máy
            # Dùng processEvents để UI không bị đơng cứng khi chờ
            for _ in range(15):  # 15 x 100ms = 1.5s
                if self._is_stopping:
                    break
                QtWidgets.QApplication.processEvents()
                QThread.msleep(100)

    def update_data(self, index, status, response_data):
        # Nếu đang trong trạng thái dừng thủ công → bỏ qua tín hiệu từ thread
        if self._is_stopping:
            print(f"[Cảnh {index}] Bỏ qua signal (tool đã dừng): {status}")
            return

        # In log ra màn hình console để theo dõi
        print(f"[Cảnh {index}] Trạng thái: {status} | Phản hồi N8N: {response_data}")
        
        if status.startswith("Lỗi"):
            all_preview_containers = []
            for tab in [self.tab_veo3, self.tab_kol]:
                all_preview_containers.extend(tab.findChildren(QtWidgets.QStackedWidget, "previewContainer"))
            for container in all_preview_containers:
                preview_widget = container.widget(0)
                if not preview_widget or not isinstance(preview_widget, QtWidgets.QLabel): continue
                label_text = preview_widget.text().strip()
                if hasattr(preview_widget, '_scene_index'):
                    is_match = (preview_widget._scene_index == index)
                else:
                    is_match = (f"SCENE {index}" in label_text.upper())
                if is_match:
                    preview_widget.setText(f"❌ {status}")
                    preview_widget.setStyleSheet("background: rgba(239, 68, 68, 0.2); color: #ef4444; font-weight: bold; border-radius: 6px;")

        if status == "Tải video thành công":
            save_path = response_data
            thumbnail_path = ""
            if "|" in response_data:
                save_path, thumbnail_path = response_data.split("|", 1)

            # Tìm tất cả previewContainer trên CẢ HAI tab để đảm bảo luôn gán đúng cảnh
            # (không phụ thuộc vào tab nào đang active trên giao diện)
            all_preview_containers = []
            for tab in [self.tab_veo3, self.tab_kie_ai]:
                containers = tab.findChildren(QtWidgets.QStackedWidget, "previewContainer")
                all_preview_containers.extend(containers)

            # Duyệt qua tất cả container để tìm đúng cảnh thứ index
            # Mỗi tab có 10 cảnh, cảnh index 1-10 nằm ở cả 2 tab
            # Gán thumbnail cho TẤT CẢ container khớp với index
            matched = False
            for container in all_preview_containers:
                # Kiểm tra widget(0) là QLabel có text "SCENE {index}" hoặc đã được gán thumbnail trước
                preview_widget = container.widget(0)
                if not preview_widget or not isinstance(preview_widget, QtWidgets.QLabel):
                    continue
                
                # Xác định container thuộc cảnh nào bằng text gốc hoặc thuộc tính lưu trữ
                label_text = preview_widget.text().strip()
                
                if hasattr(preview_widget, '_scene_index'):
                    is_match = (preview_widget._scene_index == index)
                else:
                    is_match = (f"SCENE {index}" in label_text.upper())
                
                if not is_match:
                    continue
                
                matched = True
                # Đánh dấu scene index cho lần sau
                preview_widget._scene_index = index
                
                # 1. Hiển thị ảnh Thumbnail trên preview QLabel (widget index 0)
                if thumbnail_path and os.path.exists(thumbnail_path):
                    pixmap = QtGui.QPixmap(thumbnail_path)
                    if not pixmap.isNull():
                        # Thay đổi: Dùng KeepAspectRatio để hiển thị toàn bộ khung hình, không bị cắt xén
                        scaled_pixmap = pixmap.scaled(
                            preview_widget.size(),
                            QtCore.Qt.KeepAspectRatio,
                            QtCore.Qt.SmoothTransformation
                        )
                        preview_widget.setPixmap(scaled_pixmap)
                        preview_widget.setText("")  # Xoá chữ "SCENE X" để hiển thị ảnh trọn vẹn
                        # Tắt setScaledContents để ảnh không bị bóp méo, tự động căn giữa theo pixmap đã scale
                        preview_widget.setScaledContents(False)
                        # Đổi nền thành màu đen để lấp đầy 2 bên mảng trống nếu ảnh là 9:16
                        preview_widget.setStyleSheet("background-color: #000000; border-radius: 6px; border: 1px solid #334155;")
                else:
                    # Nếu không có thumbnail thì đổi text để người dùng biết là đã tải xong và có thể click
                    preview_widget.setText("🎥 VIDEO ĐÃ TẢI XONG\n\n(Click để xem)")
                    preview_widget.setStyleSheet("background: rgba(16, 185, 129, 0.2); color: #34d399; font-weight: bold; border-radius: 6px;")

                # 2. Gán sự kiện click chuột mở video trực tiếp trong máy
                def make_clickable(widget, video_path):
                    widget.setCursor(QtCore.Qt.PointingHandCursor)
                    widget.setToolTip(f"Click để mở video: {os.path.basename(video_path)}")
                    def click_event(event, vp=video_path):
                        if os.path.exists(vp):
                            try:
                                os.startfile(vp)
                            except AttributeError:
                                import subprocess
                                import sys
                                if sys.platform == "darwin":
                                    subprocess.call(["open", vp])
                                else:
                                    subprocess.call(["xdg-open", vp])
                    widget.mousePressEvent = click_event

                make_clickable(preview_widget, save_path)
                
                video_widget = container.widget(1)
                if video_widget:
                    make_clickable(video_widget, save_path)

                # Giữ màn hình hiển thị Thumbnail (Index 0)
                container.setCurrentIndex(0)
            
            if not matched:
                print(f"[Cảnh {index}] ⚠️ Không tìm thấy previewContainer phù hợp cho cảnh {index}")

            return

        # Nếu luồng hoàn tất, bị lỗi, hoặc bị dừng thì giảm bộ đếm luồng đang chạy
        if status == "Hoàn thành" or status.startswith("Lỗi") or status == "Đã dừng":
            if status == "Hoàn thành" and response_data and response_data != "-":
                # Cập nhật text prompt cho cảnh tương ứng nếu có dữ liệu trả về
                prompt_boxes = getattr(self, "_active_run_prompt_boxes", self.scene_prompt_boxes)
                if 1 <= index <= len(prompt_boxes):
                    prompt_boxes[index - 1].setPlainText(response_data)

            # Tránh race condition: chỉ tăng nếu chưa vượt total
            if self.completed_threads < self.total_threads:
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

    def _open_output_folder(self):
        """Mở folder chứa tất cả video/ảnh đã tạo từ tool."""
        # Ưu tiên lấy đường dẫn từ ô le_folder trên giao diện
        folder_path = self.le_folder.text().strip() if hasattr(self, 'le_folder') else ""

        # Fallback: nếu ô trống, dùng thư mục videos_da_tao mặc định trong cwd
        if not folder_path:
            folder_path = os.path.join(os.getcwd(), "videos_da_tao")

        # Nếu folder chưa tồn tại thì hỏi người dùng có muốn tạo không
        if not os.path.exists(folder_path):
            reply = QMessageBox.question(
                self,
                "Thư mục chưa tồn tại",
                f"Thư mục sau chưa tồn tại:\n{folder_path}\n\nBạn có muốn tạo thư mục này không?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                try:
                    os.makedirs(folder_path, exist_ok=True)
                except Exception as e:
                    QMessageBox.critical(self, "Lỗi", f"Không thể tạo thư mục:\n{e}")
                    return
            else:
                return

        # Mở folder bằng trình quản lý file của hệ điều hành
        try:
            import subprocess
            import sys
            if sys.platform == "win32":
                os.startfile(folder_path)
            elif sys.platform == "darwin":
                subprocess.call(["open", folder_path])
            else:
                subprocess.call(["xdg-open", folder_path])
            print(f"[Thư mục xuất] ✅ Đã mở folder: {folder_path}")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể mở thư mục:\n{e}")

    def concatAllScenes(self):
        """Ghép nối tất cả video cảnh thành 1 video duy nhất."""
        # Kiểm tra nếu đang ghép thì không cho bấm lại
        if self.concat_thread and self.concat_thread.isRunning():
            QMessageBox.warning(self, "Thông báo", "Đang ghép video, vui lòng chờ!")
            return

        # Tìm thư mục chứa video đã tạo
        save_dir = self.le_folder.text().strip() if hasattr(self, 'le_folder') else ""
        if not save_dir:
            save_dir = os.path.join(os.getcwd(), "videos_da_tao")
            
        if not os.path.exists(save_dir):
            QMessageBox.warning(self, "Lỗi", "Thư mục lưu trữ video không tồn tại.\nHãy tạo video từ các cảnh trước!")
            return

        # Lấy số lượng cảnh hiện tại đang được chọn trên giao diện
        try:
            input_soluong = int(self.cb_scene_count.currentText())
        except ValueError:
            QMessageBox.warning(self, "Lỗi", "Số lượng cảnh trên giao diện không hợp lệ.")
            return
        if input_soluong > 10:
            QMessageBox.warning(self, "Lỗi", "Số lượng cảnh tối đa là 10.")
            return

        video_files = []
        missing_scenes = []

        # Chỉ tìm đúng các cảnh từ 1 đến input_soluong
        for i in range(1, input_soluong + 1):
            prefix = f"canh_{i}_"
            scene_files = []
            for f in os.listdir(save_dir):
                if f.startswith(prefix) and f.endswith(".mp4") and "_thumb" not in f and f != "video_ghep_tat_ca.mp4":
                    scene_files.append(os.path.join(save_dir, f))
            
            if scene_files:
                # Nếu có nhiều file cho cùng 1 cảnh, ưu tiên lấy file mới được tạo nhất
                scene_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                video_files.append(scene_files[0])
            else:
                missing_scenes.append(str(i))

        if missing_scenes:
            QMessageBox.warning(self, "Lỗi", f"Chưa có video cho các cảnh: {', '.join(missing_scenes)}.\nHãy chạy tạo video cho các cảnh này trước rồi mới ghép!")
            return

        print(f"[Ghép video] Tìm thấy {len(video_files)} video cảnh để ghép:")
        for vf in video_files:
            print(f"  - {os.path.basename(vf)}")

        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(save_dir, f"video_ghep_tat_ca_{timestamp}.mp4")

        # Bật animation và vô hiệu hóa nút
        self._start_concat_animation()
        self.veo3_btn_concat.setEnabled(False)
        self.kie_btn_concat.setEnabled(False)

        # Chạy thread ghép video nền
        self.concat_thread = ConcatVideoThread(video_files, output_path)
        self.concat_thread.finished.connect(self._on_concat_finished)
        self.concat_thread.start()

    def _build_local_reference_prompt_payload(self, data, source_text):
        style = data.get("phong_cach", "") or "Cartoon"
        language = data.get("ngon_ngu", "")
        source_text = str(source_text or "").strip()
        prompt_text = f"""Read and analyze the following story/content: "{source_text}".

Identify every character appearing in the story/content, then generate a single unified cinematic character lineup reference sheet containing all characters standing together in one frame.

Requirements:

Every character must have a unique and visually distinctive appearance consistent with the story/content.
If a character's appearance is not clearly described, intelligently infer a suitable design based on the story context, personality, role, era, and environment.
All characters must maintain consistent visual style, proportions, rendering quality, and design language.
Full body visible for every character from head to toe.
No cropped limbs, no cut off feet, no missing hands.
Characters standing side by side in a clean symmetrical lineup composition.
Equal spacing between characters.
No overlapping characters.
Front-facing pose.
Eye-level camera.
Orthographic character sheet feel mixed with cinematic realism.
High character readability.
Consistent proportions and scale between all characters.
Same rendering style, same material quality, same lighting setup.
Studio soft lighting with realistic cinematic lighting and shadows.
Neutral studio background or simple minimal background.
Each character must have their name clearly displayed beneath them.
Highly optimized for AI image generation and video character consistency.
Movie-quality character design.
8K ultra detailed.

Image Style: {style}
Language: {language}

Style Keywords:
cinematic character lineup, full body, character reference sheet, multiple characters standing side by side, consistent character design, symmetrical lineup composition, equal spacing between characters, front facing pose, eye-level camera, orthographic character sheet feel, same rendering style, ultra detailed, realistic lighting, studio soft lighting, movie character design, sharp focus, cinematic realism, high character readability, consistent proportions, no overlapping characters, entire body visible, 8k

Negative Prompt:
cropped body, cut off feet, cut off hands, missing limbs, extra limbs, extra fingers, fused fingers, duplicated character, overlapping characters, merged bodies, bad anatomy, distorted proportions, inconsistent proportions, inconsistent style, different art styles, blurry face, blurry details, low detail, low quality, pixelated, deformed body, malformed hands, floating limbs, asymmetrical eyes, messy composition, incorrect spacing, wrong scale, tilted camera, side pose, back pose, incomplete body, out of frame, poorly drawn face, poorly drawn hands, duplicate accessories, unrealistic lighting, oversaturated, noisy image, watermark, text artifacts, background clutter, motion blur, mutated anatomy, broken limbs, warped face, cartoonish distortion, random objects, disconnected body parts"""
        return {
            "prompt": prompt_text,
            "phong_cach": style,
            "ngon_ngu": language,
            "mo_ta_them": data.get("mo_ta_them", ""),
            "Clone Content": data.get("Clone Content", ""),
            "Clone %": data.get("Clone %", ""),
            "giong_nhan_vat": data.get("giong_nhan_vat", ""),
            "so_canh": data.get("so_canh", "")
        }

    def createReferenceImage(self):
        """Gửi request POST tới webhook để tạo ảnh tham chiếu."""
        api_url_gpm = self.le_api_url_gpm.text().strip()
        profile_text = self.te_proxy_input.toPlainText().strip()
        profiles = [p.strip() for p in profile_text.splitlines() if p.strip() and not p.strip().startswith("#")]
        first_profile = profiles[0] if profiles else ""
        save_dir = self.le_folder.text().strip()
        link_youtube = self.veo3_le_link.text().strip()
        mo_ta_them = self.veo3_le_desc.text().strip()

        if not api_url_gpm:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng nhập API URL GPM trước khi tạo ảnh tham chiếu.")
            return
        if not first_profile:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng nhập ít nhất 1 ID Profile GPM để chạy tạo ảnh.")
            return
        if not save_dir:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn thư mục lưu video/ảnh trước.")
            return
        if not link_youtube and not mo_ta_them:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng nhập link video YouTube hoặc mô tả thêm trước khi tạo ảnh tham chiếu.")
            return

        self.ref_image_api_url_gpm = api_url_gpm
        self.ref_image_profile_id = first_profile
        self.ref_image_save_dir = save_dir

        if hasattr(self, 'veo3_btn_create_ref'):
            self.veo3_btn_create_ref.setText("⏳ Đang xử lý...")
            self.veo3_btn_create_ref.setEnabled(False)

        # Thu thập 8 thông tin từ giao diện (bắt buộc gọi từ main thread)
        payload = {
            "link_youtube": link_youtube,
            "mo_ta_them": mo_ta_them,
            "mo_hinh_sinh_kich_ban": self.cb_ai_model.currentText(),
            "phong_cach": self.cb_style.currentText(),
            "ngon_ngu": self.cb_language.currentText(),
            "ty_le_copy": self.cb_copy_ratio.currentText(),
            "giong_nhan_vat": self.te_voice_desc.toPlainText().strip(),
            "so_canh": self.cb_scene_count.currentText()
        }

        if not link_youtube and mo_ta_them:
            print("[Ảnh Tham Chiếu] Không có link YouTube, dùng trực tiếp phần mô tả thêm để tạo prompt ảnh tham chiếu.")
            self.ref_image_signal.emit(True, self._build_local_reference_prompt_payload(payload, mo_ta_them))
            return

        webhook_payload = dict(payload)
        if link_youtube and not mo_ta_them:
            webhook_payload["mo_ta_them"] = "Không có mô tả thêm. Hãy phân tích nội dung từ link YouTube."

        def worker(data):
            try:
                response = None
                for attempt in range(1, REFERENCE_IMAGE_API_MAX_RETRIES + 1):
                    print(f"[Ảnh Tham Chiếu] Đang gọi webhook tạo prompt lần {attempt}/{REFERENCE_IMAGE_API_MAX_RETRIES}: {REFERENCE_IMAGE_PROMPT_WEBHOOK_URL} (timeout {REFERENCE_IMAGE_API_MAX_TIMEOUT}s)")
                    response = requests.post(
                        REFERENCE_IMAGE_PROMPT_WEBHOOK_URL,
                        json=data,
                        timeout=REFERENCE_IMAGE_API_MAX_TIMEOUT
                    )
                    if response.status_code not in REFERENCE_IMAGE_API_RETRY_STATUS_CODES:
                        break
                    if attempt < REFERENCE_IMAGE_API_MAX_RETRIES:
                        print(f"[Ảnh Tham Chiếu] HTTP {response.status_code}, chờ {REFERENCE_IMAGE_API_RETRY_DELAY_SECONDS}s rồi thử lại...")
                        time.sleep(REFERENCE_IMAGE_API_RETRY_DELAY_SECONDS)

                if response.status_code != 200:
                    self.ref_image_signal.emit(False, f"Lỗi HTTP {response.status_code}: {response.text[:500]}")
                    return

                try:
                    res_json = response.json()
                except Exception as e:
                    fallback_source = (data.get("mo_ta_them") or "").strip()
                    if fallback_source and fallback_source != "Không có mô tả thêm. Hãy phân tích nội dung từ link YouTube.":
                        print("[Ảnh Tham Chiếu] Webhook trả JSON lỗi, dùng mô tả thêm để tạo prompt ảnh tham chiếu.")
                        self.ref_image_signal.emit(True, self._build_local_reference_prompt_payload(data, fallback_source))
                        return
                    if (data.get("link_youtube") or "").strip():
                        print("[Ảnh Tham Chiếu] Webhook trả JSON lỗi, dùng link YouTube làm nguồn mô tả dự phòng.")
                        self.ref_image_signal.emit(True, self._build_local_reference_prompt_payload(data, data.get("link_youtube", "")))
                        return
                    self.ref_image_signal.emit(False, f"Lỗi parse JSON: {str(e)}\n\nResponse:\n{response.text[:200]}")
                    return
                    
                res_dict = {}
                if isinstance(res_json, list) and len(res_json) > 0 and isinstance(res_json[0], dict):
                    res_dict = res_json[0]
                elif isinstance(res_json, dict):
                    res_dict = res_json
                    
                prompt_text = res_dict.get("Prompt ảnh tham chiếu", "")
                
                if prompt_text:
                    result_payload = {
                        "prompt": prompt_text,
                        "phong_cach": res_dict.get("phong_cach", ""),
                        "ngon_ngu": res_dict.get("ngon_ngu", ""),
                        "mo_ta_them": res_dict.get("mo_ta_them", ""),
                        "Clone Content": res_dict.get("Clone Content", ""),
                        "Clone %": res_dict.get("Clone %", ""),
                        "giong_nhan_vat": res_dict.get("giong_nhan_vat", ""),
                        "so_canh": res_dict.get("so_canh", "")
                    }
                    self.ref_image_signal.emit(True, result_payload)
                else:
                    fallback_source = (data.get("mo_ta_them") or "").strip()
                    if fallback_source == "Không có mô tả thêm. Hãy phân tích nội dung từ link YouTube.":
                        fallback_source = ""
                    fallback_source = fallback_source or (data.get("link_youtube") or "").strip()
                    if fallback_source:
                        print("[Ảnh Tham Chiếu] API không có prompt ảnh tham chiếu, dùng dữ liệu nhập làm prompt dự phòng.")
                        self.ref_image_signal.emit(True, self._build_local_reference_prompt_payload(data, fallback_source))
                    else:
                        self.ref_image_signal.emit(False, f"API không có trường 'Prompt ảnh tham chiếu'. Dữ liệu trả về: {str(res_json)[:300]}")
            except requests.Timeout:
                self.ref_image_signal.emit(False, "Timeout: API tạo prompt ảnh tham chiếu không trả kết quả sau 30 phút.")
            except requests.RequestException as e:
                self.ref_image_signal.emit(False, f"Lỗi kết nối API: {str(e)}")
            except Exception as e:
                self.ref_image_signal.emit(False, f"Lỗi API: {str(e)}")
                
        import threading
        t = threading.Thread(target=worker, args=(webhook_payload,))
        t.daemon = True
        t.start()

    def _on_ref_image_result(self, success, result_text):
        def _reset_btn():
            if hasattr(self, 'veo3_btn_create_ref'):
                self.veo3_btn_create_ref.setText("🖼 Bấm để tạo hình tham chiếu nhân vật")
                self.veo3_btn_create_ref.setEnabled(True)

        if success:
            # result_text is a dictionary
            data_dict = result_text
            prompt_val = data_dict.get("prompt", "")
            
            # Save the extra fields for later use in _send_ref_image_to_webhook
            self.ref_image_extra_info = {
                "phong_cach": data_dict.get("phong_cach", ""),
                "ngon_ngu": data_dict.get("ngon_ngu", ""),
                "mo_ta_them": data_dict.get("mo_ta_them", ""),
                "Clone Content": data_dict.get("Clone Content", ""),
                "Clone %": data_dict.get("Clone %", ""),
                "giong_nhan_vat": data_dict.get("giong_nhan_vat", ""),
                "so_canh": data_dict.get("so_canh", "")
            }
            
            if hasattr(self, 'veo3_btn_create_ref'):
                self.veo3_btn_create_ref.setText("⏳ Đang tải ảnh nền Playwright...")
                # Nút vẫn đang disable từ lúc bấm, sẽ khôi phục khi ảnh tải xong
                
            if hasattr(self, 'veo3_ref_txt'):
                self.full_ref_prompt = prompt_val
                
                # Cắt ngắn văn bản nếu quá dài để không làm vỡ layout (độ dài an toàn ~ 180 ký tự)
                display_text = prompt_val
                if len(prompt_val) > 180:
                    display_text = prompt_val[:180] + "... <a href='#view_full' style='color:#60a5fa;'>[Xem đầy đủ]</a>"
                
                self.veo3_ref_txt.setTextFormat(QtCore.Qt.RichText)
                self.veo3_ref_txt.setText(display_text)
                self.veo3_ref_txt.setStyleSheet("color: white; font-weight: normal; font-style: normal;")
                
                # Tránh kết nối bị nhân bản nhiều lần nếu bấm liên tục
                try:
                    self.veo3_ref_txt.linkActivated.disconnect()
                except TypeError:
                    pass
                self.veo3_ref_txt.linkActivated.connect(self._show_full_ref_prompt)
            
            # --- CHẠY LUỒNG PLAYWRIGHT ĐỂ SINH VÀ TẢI ẢNH ---
            # Chuẩn hóa win_size sang format "W,H" (giống startThreadVeo3) trước khi truyền vào RefImageThread
            win_size_raw = self.cb_win_size.currentText()
            win_size = win_size_raw.replace("px", "").replace(":", ",")
            api_url_gpm = getattr(self, "ref_image_api_url_gpm", "").strip()
            first_profile = getattr(self, "ref_image_profile_id", "").strip()
            save_dir = getattr(self, "ref_image_save_dir", "").strip()

            if not api_url_gpm or not first_profile or not save_dir:
                _reset_btn()
                QMessageBox.warning(self, "Cảnh báo", "Thiếu API URL GPM, ID Profile hoặc thư mục lưu ảnh. Vui lòng kiểm tra lại thông tin.")
                return

            print(f"[Ảnh Tham Chiếu] Bắt đầu tiến trình tự động tải ảnh bằng Playwright...")
            self.ref_image_thread = RefImageThread(
                api_url_gpm=api_url_gpm,
                profile_id=first_profile,
                prompt_text=self.full_ref_prompt,
                win_size=win_size,
                win_pos="0,0",
                save_dir=save_dir
            )
            self.ref_image_thread.finished.connect(self._on_ref_image_created)
            self.ref_image_thread.start()
            
        else:
            _reset_btn()
            QMessageBox.critical(self, "Lỗi API", result_text)

    def _on_ref_image_created(self, success, result_msg):
        """Xử lý sau khi luồng RefImageThread kết thúc sinh ảnh và tải ảnh."""
        if hasattr(self, 'veo3_btn_create_ref'):
            self.veo3_btn_create_ref.setText("🖼 Bấm để tạo hình tham chiếu nhân vật")
            self.veo3_btn_create_ref.setEnabled(True)
            
        if success:
            image_path = result_msg
            self.reference_image_path = image_path
            print(f"[Ảnh Tham Chiếu] Tải ảnh hoàn tất: {image_path}")
            if hasattr(self, 'veo3_ref_img') and os.path.exists(image_path):
                # Load ảnh lên bằng QPixmap
                pixmap = QtGui.QPixmap(image_path)
                if not pixmap.isNull():
                    # Lấy kích thước thực tế của widget để scale vừa khít
                    widget_size = self.veo3_ref_img.size()
                    target_w = max(widget_size.width(), 150)
                    target_h = max(widget_size.height(), 100)
                    # Scale ảnh vừa khít widget, giữ tỷ lệ, không bị méo
                    scaled_pixmap = pixmap.scaled(
                        target_w,
                        target_h,
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation
                    )
                    self.veo3_ref_img.setPixmap(scaled_pixmap)
                    self.veo3_ref_img.setScaledContents(False)
                    self.veo3_ref_img.setAlignment(QtCore.Qt.AlignCenter)
                    # Xoá chữ "ẢNH THAM CHIẾU" và cập nhật style
                    self.veo3_ref_img.setText("")
                    self.veo3_ref_img.setStyleSheet(
                        "border-radius: 8px; background-color: #0f172a;"
                    )
                    print(f"[Ảnh Tham Chiếu] ✅ Hiển thị ảnh thành công lên widget veo3_ref_img.")
                else:
                    print(f"[Ảnh Tham Chiếu] ⚠️ Không đọc được ảnh từ: {image_path}")
                    self.veo3_ref_img.setText("⚠️ Ảnh lỗi")

            # --- GỌI API WEBHOOK SAU KHI CÓ ĐỦ PROMPT VÀ ẢNH ---
            prompt_text = getattr(self, 'full_ref_prompt', '')
            self._send_ref_image_to_webhook(prompt_text, image_path)

        else:
            print(f"[Ảnh Tham Chiếu] ❌ Lỗi tạo ảnh tham chiếu: {result_msg}")
            QMessageBox.warning(self, "Lỗi tải ảnh", f"Không thể sinh hoặc tải ảnh tham chiếu.\n\nChi tiết: {result_msg}")

    def _send_ref_image_to_webhook(self, prompt_text, image_path):
        """Gửi POST (multipart) đến webhook với prompt và ảnh tham chiếu. Chạy nền để không chặn UI."""
        WEBHOOK_URL = "https://n8n.aiplt.io.vn/webhook/webhook_get_hinh_tham_chieu_tool"
        extra_info = getattr(self, 'ref_image_extra_info', {})

        def _worker(prompt, img_path, extra_data):
            try:
                print(f"[Webhook Ảnh TC] Đang gửi POST đến {WEBHOOK_URL} ...")
                with open(img_path, "rb") as img_file:
                    files = {
                        "image": (os.path.basename(img_path), img_file, "image/png")
                    }
                    data = {
                        "Prompt ảnh tham chiếu": prompt,
                        "phong_cach": extra_data.get("phong_cach", ""),
                        "ngon_ngu": extra_data.get("ngon_ngu", ""),
                        "mo_ta_them": extra_data.get("mo_ta_them", ""),
                        "Clone Content": extra_data.get("Clone Content", ""),
                        "Clone %": extra_data.get("Clone %", ""),
                        "giong_nhan_vat": extra_data.get("giong_nhan_vat", ""),
                        "so_canh": extra_data.get("so_canh", "")
                    }
                    try:
                        with open("debug_webhook.txt", "w", encoding="utf-8") as df:
                            import json
                            df.write(json.dumps(data, ensure_ascii=False, indent=2))
                    except Exception as e:
                        pass
                    resp = None
                    for attempt in range(1, REFERENCE_IMAGE_API_MAX_RETRIES + 1):
                        print(f"[Webhook Ảnh TC] Đang gửi POST lần {attempt}/{REFERENCE_IMAGE_API_MAX_RETRIES} đến {WEBHOOK_URL} ...")
                        img_file.seek(0)
                        resp = requests.post(WEBHOOK_URL, data=data, files=files, timeout=REFERENCE_IMAGE_API_MAX_TIMEOUT)
                        if resp.status_code not in REFERENCE_IMAGE_API_RETRY_STATUS_CODES:
                            break
                        if attempt < REFERENCE_IMAGE_API_MAX_RETRIES:
                            print(f"[Webhook Ảnh TC] HTTP {resp.status_code}, chờ {REFERENCE_IMAGE_API_RETRY_DELAY_SECONDS}s rồi thử lại...")
                            time.sleep(REFERENCE_IMAGE_API_RETRY_DELAY_SECONDS)
                print(f"[Webhook Ảnh TC] ✅ Gửi xong — HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code == 200:
                    try:
                        res_json = resp.json()
                        self.ref_image_webhook_signal.emit(True, res_json)
                    except Exception as e:
                        print(f"[Webhook Ảnh TC] ❌ Lỗi parse JSON: {e}")
                        self.ref_image_webhook_signal.emit(False, None)
                else:
                    self.ref_image_webhook_signal.emit(False, None)
            except Exception as e:
                print(f"[Webhook Ảnh TC] ❌ Lỗi gửi webhook: {e}")
                self.ref_image_webhook_signal.emit(False, None)

        import threading
        t = threading.Thread(target=_worker, args=(prompt_text, image_path, extra_info), daemon=True)
        t.start()

    def _on_ref_image_webhook_result(self, success, result_data):
        if success:
            try:
                items = []
                def extract_items(data):
                    if isinstance(data, list):
                        for x in data:
                            extract_items(x)
                    elif isinstance(data, dict):
                        keys_lower = {str(k).lower(): k for k in data.keys()}
                        if "scenenumber" in keys_lower and "content" in keys_lower:
                            scene_num = data[keys_lower["scenenumber"]]
                            content = data[keys_lower["content"]]
                            items.append({"sceneNumber": scene_num, "content": content})
                        else:
                            for v in data.values():
                                extract_items(v)
                                
                extract_items(result_data)
                
                if not items:
                    QMessageBox.warning(self, "Lỗi", f"Không tìm thấy cấu trúc 'sceneNumber' và 'content' trong kết quả trả về!\n\nChi tiết API: {str(result_data)[:500]}")
                    return

                current_tab_index = self.tabWidget.currentIndex()
                if current_tab_index == 0:
                    target_tab = self.tab_veo3
                else:
                    target_tab = self.tab_kie_ai
                    
                prompt_boxes = target_tab.findChildren(QtWidgets.QTextEdit, "promptBox")
                updated_count = 0
                
                for item in items:
                    try:
                        scene_num = int(item.get("sceneNumber"))
                        content = str(item.get("content", ""))
                        if 1 <= scene_num <= len(prompt_boxes):
                            prompt_boxes[scene_num - 1].setPlainText(content)
                            updated_count += 1
                    except (ValueError, TypeError):
                        pass
                            
                if updated_count > 0:
                    QMessageBox.information(self, "Thành công", f"Đã cập nhật {updated_count} Prompt từ ảnh tham chiếu lên giao diện!")
                else:
                    QMessageBox.warning(self, "Lỗi", f"Tìm thấy dữ liệu nhưng số cảnh không hợp lệ hoặc lớn hơn số box trên giao diện!\nDữ liệu mẫu: {items[:2]}")
            except Exception as e:
                print(f"[Webhook Ảnh TC] Lỗi update UI: {e}")
                QMessageBox.warning(self, "Lỗi", f"Đã xảy ra lỗi khi hiển thị dữ liệu lên giao diện:\n{e}")
        else:
            if result_data:
                QMessageBox.warning(self, "Lỗi Webhook", f"Lỗi từ API Webhook:\n{result_data}")

    def _show_full_ref_prompt(self, link):
        """Hiển thị hộp thoại chứa toàn bộ Prompt nếu người dùng bấm vào '[Xem đầy đủ]'."""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Prompt ảnh tham chiếu đầy đủ")
        dialog.resize(600, 400)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        text_edit = QtWidgets.QTextEdit(dialog)
        text_edit.setPlainText(getattr(self, 'full_ref_prompt', ''))
        text_edit.setReadOnly(True)
        # Sử dụng font chữ dễ đọc hơn cho khung xem trước
        font = text_edit.font()
        font.setPointSize(11)
        text_edit.setFont(font)
        
        layout.addWidget(text_edit)
        dialog.exec_()

    def _on_concat_finished(self, success, result):
        """Callback khi thread ghép video hoàn tất."""
        # Tắt animation và trả text nút về ban đầu
        self._stop_concat_animation()
        original_text = "🎞️ Ghép tất cả cảnh thành 1 video"
        self.veo3_btn_concat.setText(original_text)
        self.kie_btn_concat.setText(original_text)
        self.veo3_btn_concat.setEnabled(True)
        self.kie_btn_concat.setEnabled(True)

        if success:
            msg = QMessageBox(self)
            msg.setWindowTitle("Thành công")
            msg.setText("Đã ghép tất cả các cảnh video thành 1 video!")
            msg.setIcon(QMessageBox.Information)
            
            btn_play = msg.addButton("Xem trực tiếp video", QMessageBox.ActionRole)
            btn_folder = msg.addButton("Mở thư mục chứa video", QMessageBox.ActionRole)
            msg.addButton("Đóng", QMessageBox.RejectRole)
            
            msg.exec_()
            
            if msg.clickedButton() == btn_play:
                try:
                    os.startfile(result)
                except AttributeError:
                    import subprocess, sys
                    if sys.platform == "darwin":
                        subprocess.call(["open", result])
                    else:
                        subprocess.call(["xdg-open", result])
            elif msg.clickedButton() == btn_folder:
                folder_path = os.path.dirname(result)
                try:
                    os.startfile(folder_path)
                except AttributeError:
                    import subprocess, sys
                    if sys.platform == "darwin":
                        subprocess.call(["open", folder_path])
                    else:
                        subprocess.call(["xdg-open", folder_path])
        else:
            QMessageBox.critical(self, "Lỗi ghép video", result)

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
    
    # ── Kiểm tra License Key trước khi vào tool ──
    from license_key_dialog import LicenseKeyDialog
    license_dialog = LicenseKeyDialog()
    if not license_dialog.exec_accepted():
        # Người dùng đóng dialog hoặc nhập sai key → thoát ứng dụng
        sys.exit(0)
    
    window = Manager()
    window.showMaximized()
    sys.exit(app.exec_())
