# -*- coding: utf-8 -*-
"""
Kie AI Auto — Module xử lý tự động tạo video cho tab "Chế độ tạo video với Kie AI".

File này chứa:
  - KieAiVideoThread: QThread chạy tự động tạo video bằng Kie AI API + GPM Playwright
  - setup_kie_ai_connections(): Hàm kết nối sự kiện cho tab Kie AI trong Manager

Import vào file chạy chính (giaodientoolmau_videoai.py):
  from kie_ai_auto import KieAiVideoThread, setup_kie_ai_connections
"""

import sys
import re
import os
import time
import threading

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QMessageBox
from PyQt5 import QtCore, QtGui, QtWidgets

from GpmGlobalApi_tuviet import Gpm

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
except ImportError:
    sync_playwright = None
    PlaywrightTimeoutError = TimeoutError
    PlaywrightError = Exception


class KieAiVideoThread(QThread):
    """
    Thread tự động tạo video bằng Kie AI.
    
    Signal record(int, str, str):
        - int:  Số thứ tự cảnh (index)
        - str:  Trạng thái ("Đang mở GPM", "Hoàn thành", "Lỗi: ...", "Đã dừng", "Tải video thành công")
        - str:  Kết quả (đường dẫn video hoặc "-")
        
    Signal pattern giống hệt MultiThread để dùng chung Manager.update_data().
    """
    record = pyqtSignal(int, str, str)

    def __init__(self, index, api_url_gpm, kie_api_key="", profile_id="",
                 prompt_text="test cảnh", win_size="800,800", win_pos="0,0",
                 save_dir="", reference_image_path="", flow_settings=None):
        super().__init__()
        self.index = index
        self.api_url_gpm = api_url_gpm
        self.kie_api_key = kie_api_key
        self.profile_id = profile_id
        self.prompt_text = prompt_text
        self.win_size = win_size
        self.win_pos = win_pos
        self.save_dir = save_dir
        self.reference_image_path = reference_image_path
        self.flow_settings = flow_settings or {}
        self.is_running = True
        self._stop_event = threading.Event()

    def run(self):
        """
        Luồng chính xử lý tự động tạo video bằng Kie AI.
        
        Quy trình:
          1. Kiểm tra thông tin đầu vào (profile_id)
          2. Mở Profile GPM
          3. Kết nối Playwright vào trình duyệt
          4. Truy cập trang Kie AI
          5. Chuẩn bị ảnh KOL/sản phẩm
          6. Nhập prompt và gửi tạo video
          7. Chờ kết quả và tải video về
          8. Đóng trình duyệt
        """
        gpm = Gpm()
        profile_id = self.profile_id

        status = None
        response_data = "-"
        try:
            # ── Bước 1: Kiểm tra ID Profile GPM ──
            if not self.is_running:
                status = "Đã dừng"
                return
            if not profile_id:
                raise RuntimeError("Chưa có ID Profile cho cảnh này.")
            # ── Bước 2: Mở Profile GPM ──
            if not self.is_running:
                status = "Đã dừng"
                return
            self.record.emit(self.index, "Đang mở GPM", "-")
            remote_addr = gpm.open_profile(
                apiurl_Gpm=self.api_url_gpm,
                id_profile=profile_id,
                win_pos=self.win_pos,
                win_size=self.win_size
            )

            if not remote_addr:
                raise RuntimeError("open_profile trả về None — GPM chưa khởi động hoặc API URL GPM sai.")

            # ── Bước 3: Kết nối Playwright ──
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
                        print(f"[Kie AI - Cảnh {self.index}] Lỗi kết nối CDP (lần {attempt+1}), thử lại sau 2s...")
                        if self._stop_event.wait(2):
                            raise InterruptedError("Đã dừng")
                if not browser:
                    raise RuntimeError("Không thể kết nối Playwright với trình duyệt.")

                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()

                # Áp dụng viewport size
                try:
                    numbers = re.findall(r'\d+', str(self.win_size))
                    if len(numbers) >= 2:
                        w, h = int(numbers[0]), int(numbers[1])
                        vp_w = w
                        vp_h = max(300, h - 85)
                        page.set_viewport_size({"width": vp_w, "height": vp_h})
                except Exception as e:
                    print(f"[Kie AI - Cảnh {self.index}] Lỗi set viewport: {e}")

                # ── Bước 4: Truy cập trang Kie AI ──
                self._check_stop()
                self.record.emit(self.index, "Đang truy cập Kie AI", "-")

                # ═══════════════════════════════════════════════════════════
                # TODO: THAY ĐỔI URL TRANG KIE AI VÀ LOGIC TỰ ĐỘNG Ở ĐÂY
                # ═══════════════════════════════════════════════════════════
                # Ví dụ: page.goto("https://kie.ai/...", timeout=60000, wait_until="domcontentloaded")
                #
                # Hiện tại dùng placeholder — bạn cần điền URL thực tế của Kie AI
                kie_ai_url = "https://kie.ai"
                try:
                    page.goto(kie_ai_url, timeout=60000, wait_until="domcontentloaded")
                except PlaywrightTimeoutError:
                    print(f"[Kie AI - Cảnh {self.index}] goto timeout, tiếp tục...")

                # Tự động zoom nếu viewport nhỏ
                try:
                    numbers = re.findall(r'\d+', str(self.win_size))
                    if len(numbers) >= 2:
                        vp_w = int(numbers[0])
                        if vp_w < 800:
                            zoom_level = round(vp_w / 800, 2)
                            zoom_level = max(0.3, min(zoom_level, 1.0))
                            page.evaluate(f"document.body.style.zoom = '{zoom_level}'")
                except Exception:
                    pass

                self._check_stop()

                # ── Bước 5: Chuẩn bị ảnh KOL/sản phẩm ──
                self.record.emit(self.index, "Đang chuẩn bị ảnh KOL/sản phẩm", "-")
                product_image_path = self.flow_settings.get("product_image_path", "")
                if product_image_path:
                    print(f"[KOL AI - Cảnh {self.index}] Ảnh sản phẩm: {product_image_path}")

                self._check_stop()

                # ── Bước 6: Upload ảnh KOL (nếu có) ──
                if self.reference_image_path and os.path.exists(self.reference_image_path):
                    self.record.emit(self.index, "Đang upload ảnh KOL", "-")
                    # ═══════════════════════════════════════════════════════════
                    # TODO: LOGIC UPLOAD ẢNH THAM CHIẾU LÊN KIE AI
                    # ═══════════════════════════════════════════════════════════
                    # Ví dụ:
                    # file_input = page.locator('input[type="file"]').first
                    # file_input.set_input_files(self.reference_image_path)
                    print(f"[KOL AI - Cảnh {self.index}] Ảnh KOL: {self.reference_image_path}")

                self._check_stop()

                # ── Bước 7: Nhập prompt và gửi tạo video ──
                self.record.emit(self.index, "Đang nhập prompt", "-")
                # ═══════════════════════════════════════════════════════════
                # TODO: LOGIC NHẬP PROMPT VÀ BẤM TẠO VIDEO
                # ═══════════════════════════════════════════════════════════
                # Ví dụ:
                # prompt_input = page.locator('textarea').first
                # prompt_input.fill(self.prompt_text)
                # page.locator('button:has-text("Generate")').click()
                print(f"[Kie AI - Cảnh {self.index}] Prompt: {self.prompt_text[:50]}...")

                if self._stop_event.wait(1):
                    raise InterruptedError("Đã dừng")

                # ── Bước 8: Chờ quá trình tạo video hoàn tất ──
                self.record.emit(self.index, "Đang chờ Kie AI tạo video", "-")
                # ═══════════════════════════════════════════════════════════
                # TODO: LOGIC CHỜ VIDEO ĐƯỢC TẠO XONG
                # ═══════════════════════════════════════════════════════════
                # Ví dụ vòng lặp chờ (giống pattern MultiThread):
                timeout = 300  # 5 phút
                elapsed = 0
                has_started_generating = False
                disappear_count = 0

                while elapsed < timeout:
                    self._check_stop()

                    # Kiểm tra lỗi
                    # try:
                    #     error_loc = page.locator('text="Error"').first
                    #     if error_loc.is_visible():
                    #         raise RuntimeError("Kie AI báo lỗi khi tạo video.")
                    # except RuntimeError:
                    #     raise
                    # except Exception:
                    #     pass

                    # Kiểm tra tiến trình
                    # try:
                    #     is_generating = page.locator('[role="progressbar"]').first.is_visible()
                    # except Exception:
                    #     is_generating = False

                    # if is_generating:
                    #     has_started_generating = True
                    #     disappear_count = 0
                    # else:
                    #     if has_started_generating:
                    #         disappear_count += 2
                    #         if disappear_count >= 16:
                    #             break
                    #     elif elapsed > 60:
                    #         break

                    if self._stop_event.wait(2):
                        raise InterruptedError("Đã dừng")
                    elapsed += 2

                    # Tạm thời break ngay (xóa dòng này khi đã có logic thật)
                    break

                # ── Bước 9: Tải video về máy ──
                self.record.emit(self.index, "Đang tải video", "-")
                save_dir = self.save_dir if self.save_dir else os.path.join(os.getcwd(), "videos_da_tao")
                if not os.path.exists(save_dir):
                    try:
                        os.makedirs(save_dir, exist_ok=True)
                    except Exception:
                        pass

                safe_prompt = re.sub(r'[\\/*?:"<>|\n\r]', " ", self.prompt_text)
                safe_prompt = safe_prompt[:30].strip() if safe_prompt else "video"
                filename = f"kie_canh_{self.index}_{safe_prompt}.mp4"
                save_path = os.path.join(save_dir, filename)

                # ═══════════════════════════════════════════════════════════
                # TODO: LOGIC TẢI VIDEO TỪ KIE AI VỀ MÁY
                # ═══════════════════════════════════════════════════════════
                # Ví dụ (giống pattern MultiThread):
                # video_url = page.evaluate('''...tìm URL video...''')
                # if video_url.startswith('blob:'):
                #     base64_data = page.evaluate(f'''fetch("{video_url}")...''')
                #     ...ghi file...
                # else:
                #     resp = context.request.get(video_url)
                #     with open(save_path, "wb") as f:
                #         f.write(resp.body())

                # Tạm thời: Ghi file placeholder (xóa khi có logic thật)
                print(f"[Kie AI - Cảnh {self.index}] TODO: Tải video về {save_path}")

                # Chụp thumbnail (giống pattern MultiThread)
                thumbnail_path = ""
                # try:
                #     thumb_filename = f"kie_canh_{self.index}_{safe_prompt}_thumb.png"
                #     t_path = os.path.join(save_dir, thumb_filename)
                #     page.wait_for_timeout(3000)
                #     ...chụp screenshot...
                #     thumbnail_path = t_path
                # except Exception:
                #     pass

                # Emit kết quả thành công
                # emit_data = f"{save_path}|{thumbnail_path}" if thumbnail_path else save_path
                # self.record.emit(self.index, "Tải video thành công", emit_data)

                # Sau khi xong, đánh dấu hoàn thành
                status = "Hoàn thành"
                print(f"[Kie AI - Cảnh {self.index}] ✅ Hoàn thành quy trình Kie AI")
                browser.close()

        except InterruptedError:
            print(f"[Kie AI - Cảnh {self.index}] Luồng đã bị ngắt bởi người dùng.")
            status = "Đã dừng"
            response_data = "-"
        except PlaywrightError as e:
            if "TargetClosedError" in str(e.__class__) or "has been closed" in str(e):
                print(f"[Kie AI - Cảnh {self.index}] Trình duyệt đã bị đóng.")
                status = "Đã dừng"
                response_data = "-"
            else:
                import traceback
                print(f"[Kie AI - Cảnh {self.index}] Lỗi Playwright:\n{traceback.format_exc()}")
                status = f"Lỗi: {e}"
                response_data = "-"
        except Exception as e:
            if "TargetClosedError" in str(e.__class__) or "has been closed" in str(e):
                print(f"[Kie AI - Cảnh {self.index}] Trình duyệt đã bị đóng.")
                status = "Đã dừng"
                response_data = "-"
            else:
                import traceback
                print(f"[Kie AI - Cảnh {self.index}] EXCEPTION:\n{traceback.format_exc()}")
                status = f"Lỗi: {e}"
                response_data = "-"
        finally:
            # Đóng profile GPM sau khi làm xong
            if profile_id:
                self._cleanup_profile(gpm, profile_id)
            if status:
                self.record.emit(self.index, status, response_data)

    def _cleanup_profile(self, gpm, profile_id):
        """Dọn dẹp: đóng profile GPM sau khi hoàn tất."""
        close_attempts = 3
        close_success = False
        for attempt in range(1, close_attempts + 1):
            try:
                response = gpm.close_profile(apiurl_Gpm=self.api_url_gpm, id_profile=profile_id)
                if isinstance(response, dict) and response.get("success") is False:
                    if response.get("message") in ["OK", "Profile is not running", ""]:
                        print(f"[Kie AI - Cảnh {self.index}] ✅ Profile {profile_id} đã được đóng trước đó.")
                        close_success = True
                        break
                    raise RuntimeError(f"close_profile trả về lỗi: {response}")
                print(f"[Kie AI - Cảnh {self.index}] ✅ Đã close_profile: {profile_id} (lần {attempt})")
                close_success = True
                break
            except Exception as e:
                print(f"[Kie AI - Cảnh {self.index}] ⚠️ close_profile thất bại lần {attempt}: {e}")
                time.sleep(2)
        if not close_success:
            print(f"[Kie AI - Cảnh {self.index}] ⚠️ close_profile không thành công sau {close_attempts} lần.")
        return True

    def stop(self):
        """Ra hiệu dừng: đặt cả boolean lẫn Event để ngắt sleep ngay lập tức."""
        self.is_running = False
        self._stop_event.set()
        try:
            gpm = Gpm()
            gpm.close_profile(apiurl_Gpm=self.api_url_gpm, id_profile=self.profile_id)
        except Exception:
            pass

    def _check_stop(self):
        """Kiểm tra và ném lỗi nếu người dùng yêu cầu dừng."""
        if not self.is_running:
            raise InterruptedError("Đã dừng")


def setup_kie_ai_connections(manager):
    """
    Kết nối sự kiện cho tab Kie AI trong Manager.
    
    Gọi hàm này trong Manager.__init__() SAU khi đã setupUi().
    Hàm này sẽ:
      - Kết nối nút "Bắt đầu tạo video" trên tab Kie AI → startThreadKieAi
      - Thêm method startThreadKieAi vào manager
    
    Args:
        manager: Instance của class Manager (QMainWindow)
    """
    # Gắn method startThreadKieAi vào manager instance
    import types
    manager.startThreadKieAi = types.MethodType(_startThreadKieAi, manager)
    manager.kie_ai_threads = []  # Danh sách riêng cho các thread Kie AI

    # Ngắt kết nối cũ (startThreadVeo3) và kết nối mới cho nút trên tab Kie AI
    if hasattr(manager, "kie_btn_analyze"):
        try:
            manager.kie_btn_analyze.clicked.disconnect()
        except Exception:
            pass
        manager.kie_btn_analyze.clicked.connect(manager.startThreadKieAi)


def _startThreadKieAi(self):
    """
    Method được gắn vào Manager — xử lý bấm nút "Bắt đầu tạo video" trên tab Kie AI.
    
    Logic giống startThreadVeo3 nhưng dùng KieAiVideoThread thay vì MultiThread,
    và truyền thêm kie_api_key.
    """
    # Nếu đang có luồng chạy → bấm nút = dừng tất cả
    if any(t.isRunning() for t in self.threads):
        self._stop_all_veo3_threads()
        return

    # Reset flag dừng
    self._is_stopping = False

    # Lấy số lượng cảnh
    try:
        input_soluong = int(self.cb_scene_count.currentText())
    except ValueError:
        QMessageBox.warning(self, "Lỗi", "Số lượng cảnh không hợp lệ.")
        return

    if input_soluong > 10:
        QMessageBox.warning(self, "Lỗi", "Số lượng cảnh tối đa là 10.")
        return

    kie_api_key = ""

    # Lấy API URL GPM
    input_api_url_gpm = self.le_api_url_gpm.text().strip()
    if not input_api_url_gpm:
        QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập API URL GPM (ví dụ: http://localhost:9495).")
        return

    # Kiểm tra ảnh đầu vào cho tab KOL AI
    raw_reference_image_path = self.kie_le_kol_ref_image.text().strip() if hasattr(self, "kie_le_kol_ref_image") else ""
    reference_image_path = os.path.abspath(raw_reference_image_path) if raw_reference_image_path else ""
    if not reference_image_path or not os.path.exists(reference_image_path):
        QMessageBox.warning(
            self,
            "Thiếu hình tham chiếu KOL",
            "Vui lòng chọn file Hình tham chiếu đa chiều của KOL trước khi tạo video."
        )
        return

    raw_product_image_path = self.kie_le_product_image.text().strip() if hasattr(self, "kie_le_product_image") else ""
    product_image_path = os.path.abspath(raw_product_image_path) if raw_product_image_path else ""
    if not product_image_path or not os.path.exists(product_image_path):
        QMessageBox.warning(
            self,
            "Thiếu hình ảnh sản phẩm",
            "Vui lòng chọn file Hình ảnh sản phẩm trước khi tạo video."
        )
        return

    # Đọc danh sách Profile ID
    profile_id_list = []
    proxy_text = self.te_proxy_input.toPlainText().strip()
    if proxy_text:
        for line in proxy_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            profile_id_list.append(line)
    valid_count = sum(1 for p_item in profile_id_list if p_item)
    print(f"[Kie AI Manager] Đọc được {valid_count}/{len(profile_id_list)} ID Profile.")

    # Kiểm tra số lượng Profile
    invalid_profile_format = any(
        p_item and not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", p_item)
        for p_item in profile_id_list
    )
    if valid_count < input_soluong or invalid_profile_format:
        QMessageBox.warning(
            self,
            "Kiểm tra lại danh sách Profile",
            "Vui lòng kiểm tra lại danh sách Profile. Số Profile đang ít hơn số cảnh hoặc định dạng điền trong danh sách Profile không đúng"
        )
        return

    # Khởi tạo trạng thái
    self.total_threads = input_soluong
    self.completed_threads = 0
    self.running_threads = input_soluong
    self._active_run_prompt_boxes = self._get_active_scene_prompt_boxes()

    # Đổi nút sang trạng thái đang chạy
    self._set_veo3_btn_running(True)
    self._animate_loading_button()
    QtWidgets.QApplication.processEvents()

    self.threads = []

    input_win_size_raw = self.cb_win_size.currentText()
    input_win_size = input_win_size_raw.replace("px", "").replace(":", ",")

    try:
        win_w, win_h = map(int, input_win_size.split(","))
    except Exception:
        win_w, win_h = 800, 800

    # Lấy kích thước màn hình
    screen = QtWidgets.QApplication.primaryScreen()
    if screen:
        screen_rect = screen.availableGeometry()
    else:
        screen_rect = QtCore.QRect(0, 0, 1920, 1080)
    screen_width = screen_rect.width()
    cols = max(1, screen_width // win_w)

    # Lấy thông số flow (nếu cần)
    flow_settings = {
        "content_type": self.cb_flow_content_type.currentText(),
        "frame_type": self.cb_flow_frame_type.currentText(),
        "aspect_ratio": self.cb_flow_aspect_ratio.currentText(),
        "gen_count": self.cb_flow_gen_count.currentText(),
        "ai_model": self.cb_flow_ai_model.currentText(),
        "kol_reference_image_path": reference_image_path,
        "product_image_path": product_image_path,
    }

    save_dir = self.le_folder.text().strip() if hasattr(self, 'le_folder') else ""

    # Khởi tạo KieAiVideoThread cho từng cảnh
    for i in range(1, input_soluong + 1):
        if self._is_stopping:
            break

        profile_id = profile_id_list[i - 1] if (i - 1) < len(profile_id_list) else ""

        # Tính toán vị trí hiển thị
        idx = i - 1
        col = idx % cols
        row = idx // cols
        pos_x = col * win_w
        pos_y = row * win_h
        win_pos = f"{pos_x},{pos_y}"

        # Lấy prompt từ giao diện
        current_prompt = "test cảnh"
        if (i - 1) < len(self._active_run_prompt_boxes):
            text = self._active_run_prompt_boxes[i - 1].toPlainText().strip()
            if text:
                current_prompt = text

        thread = KieAiVideoThread(
            index=i,
            api_url_gpm=input_api_url_gpm,
            kie_api_key=kie_api_key,
            profile_id=profile_id,
            prompt_text=current_prompt,
            win_size=input_win_size,
            win_pos=win_pos,
            save_dir=save_dir,
            reference_image_path=reference_image_path,
            flow_settings=flow_settings
        )

        # Dùng chung update_data() của Manager (signal cùng format)
        thread.record.connect(self.update_data)
        self.threads.append(thread)
        thread.start()

        # Delay 1.5 giây giữa các luồng
        from PyQt5.QtCore import QThread as _QThread
        for _ in range(15):
            if self._is_stopping:
                break
            QtWidgets.QApplication.processEvents()
            _QThread.msleep(100)
