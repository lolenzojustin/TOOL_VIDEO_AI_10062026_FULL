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
import base64

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
        return self._run_google_flow_kol()

    def _run_google_flow_kol(self):
        """Run the KOL AI workflow on Google Flow, following KOL_AI_FLOW_SCRIPT.md."""
        gpm = Gpm()
        profile_id = self.profile_id
        browser = None
        status = None
        response_data = "-"

        try:
            if sync_playwright is None:
                raise RuntimeError("Playwright chua duoc cai dat trong moi truong nay.")
            if not profile_id:
                raise RuntimeError("Chua co ID Profile cho canh nay.")
            if not self.reference_image_path or not os.path.exists(self.reference_image_path):
                raise RuntimeError("Khong tim thay file Hinh tham chieu da chieu cua KOL.")

            product_image_path = self.flow_settings.get("product_image_path", "")
            if not product_image_path or not os.path.exists(product_image_path):
                raise RuntimeError("Khong tim thay file Hinh anh san pham.")

            self._emit("Dang mo GPM")
            remote_addr = gpm.open_profile(
                apiurl_Gpm=self.api_url_gpm,
                id_profile=profile_id,
                win_pos=self.win_pos,
                win_size=self.win_size
            )
            if not remote_addr:
                raise RuntimeError("open_profile tra ve None. Hay kiem tra GPM API URL va trang thai GPM.")

            self._emit("Dang chay Playwright")
            with sync_playwright() as p:
                for attempt in range(1, 6):
                    self._check_stop()
                    try:
                        browser = p.chromium.connect_over_cdp(f"http://{remote_addr}")
                        break
                    except Exception:
                        self._log(f"Khong ket noi duoc CDP lan {attempt}, thu lai sau 2s")
                        if self._stop_event.wait(2):
                            raise InterruptedError("Đã dừng")

                if not browser:
                    raise RuntimeError("Khong the ket noi Playwright voi trinh duyet GPM.")

                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.pages[0] if context.pages else context.new_page()
                self._apply_viewport(page)

                self._emit("Dang vao Google Flow")
                try:
                    page.goto("https://labs.google/fx/vi/tools/flow", wait_until="domcontentloaded", timeout=60000)
                except PlaywrightTimeoutError:
                    self._log("Google Flow load cham, tiep tuc voi trang hien tai")
                page.wait_for_timeout(3000)

                self._close_optional_popups(page)
                self._open_new_flow_project(page)
                self._configure_flow_settings(page)

                self._upload_image_to_prompt(page, self.reference_image_path, "anh KOL")
                self._upload_image_to_prompt(page, product_image_path, "anh san pham")

                self._fill_prompt_and_send(page)
                self._wait_for_video_ready(page, timeout_seconds=600)

                self._emit("Dang tai video")
                save_dir = self.save_dir if self.save_dir else os.path.join(os.getcwd(), "videos_da_tao")
                os.makedirs(save_dir, exist_ok=True)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                save_path = os.path.join(save_dir, f"kol_ai_scene_{self.index:03d}_{timestamp}.mp4")
                thumbnail_path = os.path.join(save_dir, f"kol_ai_scene_{self.index:03d}_{timestamp}_thumb.png")

                thumbnail_path = self._capture_thumbnail(page, thumbnail_path)
                self._download_latest_video(context, page, save_path)

                emit_data = f"{save_path}|{thumbnail_path}" if thumbnail_path else save_path
                self.record.emit(self.index, "Tải video thành công", emit_data)
                self._log(f"Tai video thanh cong: {save_path}")

                status = "Hoàn thành"

        except InterruptedError:
            status = "Đã dừng"
            response_data = "-"
        except PlaywrightError as e:
            if "TargetClosedError" in str(e.__class__) or "has been closed" in str(e):
                status = "Đã dừng"
            else:
                import traceback
                self._log(f"Loi Playwright:\n{traceback.format_exc()}")
                status = f"Lỗi: {e}"
            response_data = "-"
        except Exception as e:
            if "TargetClosedError" in str(e.__class__) or "has been closed" in str(e):
                status = "Đã dừng"
            else:
                import traceback
                self._log(f"EXCEPTION:\n{traceback.format_exc()}")
                status = f"Lỗi: {e}"
            response_data = "-"
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            if profile_id:
                self._cleanup_profile(gpm, profile_id)
            if status:
                self.record.emit(self.index, status, response_data)

    def _log(self, message):
        print(f"[KOL AI - Canh {self.index}] {message}")

    def _emit(self, status, data="-"):
        self._log(status)
        self.record.emit(self.index, status, data)

    def _apply_viewport(self, page):
        try:
            numbers = re.findall(r"\d+", str(self.win_size))
            if len(numbers) >= 2:
                width, height = int(numbers[0]), int(numbers[1])
                page.set_viewport_size({"width": width, "height": max(300, height - 85)})
                if width < 800:
                    zoom_level = max(0.3, min(round(width / 800, 2), 1.0))
                    page.evaluate(f"document.body.style.zoom = '{zoom_level}'")
        except Exception as e:
            self._log(f"Khong set duoc viewport: {e}")

    def _close_optional_popups(self, page):
        patterns = [
            r"Got it|I agree|Accept|Continue|Skip|Close",
            r"Dong|Đóng|Da hieu|Đã hiểu|Chap nhan|Chấp nhận|Bo qua|Bỏ qua|Tiep tuc|Tiếp tục",
        ]
        for pattern in patterns:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if btn.is_visible(timeout=1200):
                    btn.click(timeout=1500)
                    page.wait_for_timeout(500)
            except Exception:
                pass

    def _click_text(self, page, pattern, timeout=8000):
        locators = [
            lambda: page.get_by_role("button", name=re.compile(pattern, re.I)).last,
            lambda: page.get_by_text(re.compile(pattern, re.I)).last,
            lambda: page.locator(f"text=/{pattern}/i").last,
        ]
        last_error = None
        for make_locator in locators:
            self._check_stop()
            try:
                locator = make_locator()
                locator.click(timeout=timeout)
                return True
            except Exception as e:
                last_error = e
        if last_error:
            self._log(f"Khong click duoc text /{pattern}/: {last_error}")
        return False

    def _open_new_flow_project(self, page):
        self._emit("Dang bam Du an moi")
        page.wait_for_timeout(2000)
        if self._click_text(page, r"Dự án mới|Du an moi|New project|New", timeout=30000):
            page.wait_for_timeout(5000)
            return

        selectors = [
            'button:has-text("+")',
            '[aria-label*="New"]',
            '[aria-label*="Dự án"]',
            '[aria-label*="Du an"]',
            '[data-testid*="new"]',
        ]
        for selector in selectors:
            try:
                page.locator(selector).last.click(timeout=5000)
                page.wait_for_timeout(5000)
                return
            except Exception:
                pass
        raise RuntimeError("Khong tim thay nut Du an moi tren Google Flow.")

    def _configure_flow_settings(self, page):
        settings = [
            self.flow_settings.get("content_type", "Video"),
            self.flow_settings.get("frame_type", ""),
            self.flow_settings.get("aspect_ratio", ""),
            self.flow_settings.get("gen_count", ""),
            self.flow_settings.get("ai_model", ""),
            self.flow_settings.get("duration", ""),
        ]
        settings = [str(value).strip() for value in settings if str(value).strip()]
        if not settings:
            return

        self._emit("Dang cau hinh Flow")
        opened = False
        for selector in [
            'button:has-text("Video")',
            'button:has-text("Hình ảnh")',
            'button:has-text("Hinh anh")',
            'button:has-text("8s")',
            'button:has-text("1x")',
            'button:has-text("x4")',
            '[aria-label*="Settings"]',
            '[aria-label*="Cài đặt"]',
        ]:
            try:
                page.locator(selector).last.click(timeout=2500)
                opened = True
                page.wait_for_timeout(600)
                break
            except Exception:
                pass

        if not opened:
            self._log("Khong mo duoc menu cau hinh Flow, tiep tuc voi cau hinh mac dinh")

        for value in settings:
            try:
                pattern = re.compile(rf"^\s*{re.escape(value)}\s*$", re.I)
                page.get_by_text(pattern).last.click(timeout=2500)
                self._log(f"Da chon setting Flow: {value}")
                page.wait_for_timeout(500)
            except Exception as e:
                self._log(f"Khong chon duoc setting Flow '{value}': {e}")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

    def _open_agent_image_picker(self, page):
        if not self._click_prompt_plus(page, timeout_ms=2500):
            self._log("Khong thay dau + san tren prompt, thu bam Tac nhan de hien dau +")
            if not self._click_text(page, r"Tác nhân|Tac nhan|Agent", timeout=8000):
                self._log("Khong thay nut Tac nhan, tiep tuc thu tim dau +")
            if not self._click_prompt_plus(page, timeout_ms=5000):
                raise RuntimeError("Khong tim thay dau + trong khu vuc prompt cua Google Flow.")

    def _click_image_upload_option(self, page, timeout=8000):
        return self._click_text(page, r"Hình ảnh|Hinh anh|Image", timeout=timeout)

    def _click_prompt_plus(self, page, timeout_ms=4000):
        end_time = time.time() + (timeout_ms / 1000)
        while time.time() < end_time:
            self._check_stop()
            clicked = page.evaluate("""
                () => {
                    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
                    const candidates = Array.from(document.querySelectorAll('button, [role="button"]'));
                    const visible = candidates
                        .map((el) => {
                            const rect = el.getBoundingClientRect();
                            const text = (el.innerText || el.textContent || '').trim();
                            const aria = (el.getAttribute('aria-label') || '').trim();
                            const title = (el.getAttribute('title') || '').trim();
                            const label = `${text} ${aria} ${title}`;
                            return {el, rect, label};
                        })
                        .filter((item) => {
                            const style = window.getComputedStyle(item.el);
                            if (style.visibility === 'hidden' || style.display === 'none') return false;
                            if (item.rect.width <= 0 || item.rect.height <= 0) return false;
                            if (item.rect.bottom < 0 || item.rect.top > viewportHeight) return false;
                            return /(^|\\s)\\+(\\s|$)|Add|Thêm|Them/i.test(item.label);
                        });

                    const promptArea = visible
                        .filter((item) => item.rect.top > viewportHeight * 0.45)
                        .sort((a, b) => b.rect.top - a.rect.top || a.rect.left - b.rect.left);
                    const target = promptArea[0] || visible.sort((a, b) => b.rect.top - a.rect.top)[0];
                    if (!target) return false;

                    target.el.scrollIntoView({block: 'center', inline: 'center'});
                    target.el.click();
                    return true;
                }
            """)
            if clicked:
                page.wait_for_timeout(700)
                return True
            page.wait_for_timeout(300)
        return False

    def _upload_image_to_prompt(self, page, image_path, label):
        self._check_stop()
        self._emit(f"Dang upload {label}")
        if not image_path or not os.path.exists(image_path):
            raise RuntimeError(f"Khong tim thay file {label}: {image_path}")

        uploaded = False
        self._open_agent_image_picker(page)

        try:
            file_input = page.locator('input[type="file"]').last
            file_input.set_input_files(image_path, timeout=2500)
            uploaded = True
        except Exception:
            pass

        if uploaded:
            self._wait_upload_ready(page)
            self._log(f"Da upload {label}: {image_path}")
            return

        try:
            with page.expect_file_chooser(timeout=5000) as fc_info:
                if not self._click_image_upload_option(page, timeout=5000):
                    raise RuntimeError("Khong tim thay nut Hinh anh.")
            fc_info.value.set_files(image_path)
            uploaded = True
        except Exception:
            pass

        if not uploaded:
            try:
                self._click_image_upload_option(page, timeout=3000)
            except Exception:
                pass

            try:
                file_input = page.locator('input[type="file"]').last
                file_input.set_input_files(image_path, timeout=6000)
                uploaded = True
            except Exception as e:
                raise RuntimeError(f"Khong upload duoc {label}: {e}")

        self._wait_upload_ready(page)
        self._log(f"Da upload {label}: {image_path}")

    def _wait_upload_ready(self, page, timeout_seconds=90):
        start = time.time()
        while time.time() - start < timeout_seconds:
            self._check_stop()
            try:
                busy = page.locator('[role="progressbar"], text=/Uploading|Đang tải|Dang tai|Processing|Đang xử lý|Dang xu ly/i').first
                if busy.is_visible(timeout=800):
                    page.wait_for_timeout(1200)
                    continue
            except Exception:
                pass
            page.wait_for_timeout(1200)
            return
        raise RuntimeError("Upload anh qua thoi gian cho phep.")

    def _fill_prompt_and_send(self, page):
        self._emit("Dang nhap prompt")
        prompt = (self.prompt_text or "").strip()
        if not prompt:
            raise RuntimeError("Prompt cua canh dang rong.")

        prompt_box = None
        for selector in [
            'textarea:visible',
            'div[contenteditable="true"]:visible',
            '[role="textbox"]:visible',
        ]:
            try:
                loc = page.locator(selector).last
                loc.click(timeout=5000)
                prompt_box = loc
                break
            except Exception:
                pass
        if prompt_box is None:
            raise RuntimeError("Khong tim thay o nhap prompt tren Google Flow.")

        try:
            prompt_box.fill(prompt, timeout=5000)
        except Exception:
            page.keyboard.insert_text(prompt)

        page.wait_for_timeout(500)
        sent = False
        for key in ["Enter", "Control+Enter"]:
            try:
                prompt_box.press(key)
                sent = True
                page.wait_for_timeout(2500)
                break
            except Exception:
                pass

        if not sent:
            for selector in [
                '[aria-label*="Send"]',
                '[aria-label*="Gửi"]',
                '[aria-label*="Gui"]',
                'button:has-text("Generate")',
                'button:has-text("Tạo")',
                'button:has-text("Tao")',
            ]:
                try:
                    page.locator(selector).last.click(timeout=4000)
                    sent = True
                    break
                except Exception:
                    pass
        if not sent:
            raise RuntimeError("Khong gui duoc prompt tao video.")

    def _wait_for_video_ready(self, page, timeout_seconds=600):
        self._emit("Dang tao video")
        started = False
        quiet_seconds = 0
        start = time.time()

        while time.time() - start < timeout_seconds:
            self._check_stop()

            try:
                error_loc = page.locator('text=/Không thành công|Khong thanh cong|Rất tiếc|Rat tiec|failed|error/i').first
                if error_loc.is_visible(timeout=1000):
                    raise RuntimeError("Google Flow bao loi khi tao video.")
            except RuntimeError:
                raise
            except Exception:
                pass

            if self._find_video_url(page)[0]:
                if started:
                    quiet_seconds += 2
                    if quiet_seconds >= 8:
                        self._log("Da thay video output")
                        return
                else:
                    started = True

            generating = False
            try:
                progress = page.locator('[role="progressbar"], text=/^\\d{1,3}%$/, text=/Đang|Dang|Generating|In queue|hàng đợi|hang doi/i').first
                generating = progress.is_visible(timeout=800)
            except Exception:
                pass
            if generating:
                started = True
                quiet_seconds = 0

            if self._stop_event.wait(2):
                raise InterruptedError("Đã dừng")

        raise RuntimeError("Cho Google Flow tao video qua thoi gian cho phep.")

    def _find_video_url(self, page):
        for frame in page.frames:
            try:
                vids = frame.locator("video").all()
                for vid in reversed(vids):
                    url = vid.evaluate("el => el.src || (el.querySelector('source') ? el.querySelector('source').src : '')")
                    if url:
                        return url, frame
            except Exception:
                pass

        for frame in page.frames:
            try:
                url = frame.evaluate("""
                    () => {
                        function findVideos(root) {
                            let vids = Array.from(root.querySelectorAll('video'));
                            for (const el of root.querySelectorAll('*')) {
                                if (el.shadowRoot) vids = vids.concat(findVideos(el.shadowRoot));
                            }
                            return vids;
                        }
                        const videos = findVideos(document);
                        for (let i = videos.length - 1; i >= 0; i--) {
                            const v = videos[i];
                            const src = v.src || (v.querySelector('source') ? v.querySelector('source').src : '');
                            if (src) return src;
                        }
                        return null;
                    }
                """)
                if url:
                    return url, frame
            except Exception:
                pass
        return None, None

    def _capture_thumbnail(self, page, thumbnail_path):
        try:
            page.wait_for_timeout(2500)
            box = page.evaluate("""
                () => {
                    function findVideos(root) {
                        let vids = Array.from(root.querySelectorAll('video'));
                        for (const el of root.querySelectorAll('*')) {
                            if (el.shadowRoot) vids = vids.concat(findVideos(el.shadowRoot));
                        }
                        return vids;
                    }
                    const videos = findVideos(document);
                    for (let i = videos.length - 1; i >= 0; i--) {
                        let el = videos[i];
                        while (el && el !== document.body) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 100 && rect.height > 100) {
                                el.scrollIntoView({block: 'center', inline: 'center'});
                                const finalRect = el.getBoundingClientRect();
                                return {x: finalRect.x, y: finalRect.y, width: finalRect.width, height: finalRect.height};
                            }
                            el = el.parentElement || (el.getRootNode() && el.getRootNode().host);
                        }
                    }
                    return null;
                }
            """)
            page.wait_for_timeout(800)
            if box:
                page.screenshot(path=thumbnail_path, clip=box)
                return thumbnail_path
        except Exception as e:
            self._log(f"Khong chup duoc thumbnail: {e}")
        return ""

    def _download_latest_video(self, context, page, save_path):
        video_url, target_frame = self._find_video_url(page)
        if not video_url:
            raise RuntimeError("Khong tim thay video output tren Google Flow.")

        self._log(f"Da lay URL video: {video_url[:90]}...")
        if video_url.startswith("blob:"):
            frame = target_frame or page
            base64_data = frame.evaluate(f"""
                async () => {{
                    const response = await fetch("{video_url}");
                    const blob = await response.blob();
                    return await new Promise((resolve, reject) => {{
                        const reader = new FileReader();
                        reader.onloadend = () => resolve(reader.result);
                        reader.onerror = reject;
                        reader.readAsDataURL(blob);
                    }});
                }}
            """)
            if not base64_data or "," not in base64_data:
                raise RuntimeError("Du lieu blob video tra ve khong hop le.")
            _, encoded = base64_data.split(",", 1)
            with open(save_path, "wb") as f:
                f.write(base64.b64decode(encoded))
            return

        resp = context.request.get(video_url)
        if not resp.ok:
            raise RuntimeError(f"Loi tai video HTTP {resp.status} - {resp.status_text}")
        with open(save_path, "wb") as f:
            f.write(resp.body())
        return

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

    missing_prompt_scenes = []
    for i in range(1, input_soluong + 1):
        if (i - 1) >= len(self._active_run_prompt_boxes):
            missing_prompt_scenes.append(str(i))
            continue
        if not self._active_run_prompt_boxes[i - 1].toPlainText().strip():
            missing_prompt_scenes.append(str(i))

    if missing_prompt_scenes:
        QMessageBox.warning(
            self,
            "Thieu prompt cho canh",
            "Vui long bam 'Bat dau phan tich tao Prompt' truoc, hoac nhap prompt thu cong cho cac canh: "
            + ", ".join(missing_prompt_scenes)
        )
        return

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
        "duration": self.cb_flow_duration.currentText(),
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
