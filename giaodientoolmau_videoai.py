import sys
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QMessageBox

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import os
import time
import threading

# Import các file phụ trợ
from tool_video_ai_layout_3_UI import Ui_Widget
from GpmGlobalApi_tuviet import Gpm

class MultiThread(QThread):
    # Khai báo signal trả về: (Số thứ tự cảnh, trạng thái, kết quả)
    record = pyqtSignal(int, str, str)

    def __init__(self, index, api_url_gpm, proxy=""):
        super().__init__()
        self.index = index
        self.api_url_gpm = api_url_gpm  # URL GPM lấy từ giao diện
        self.proxy = proxy               # Proxy cho luồng này (rỗng = dùng proxy local)
        self.is_running = True
        self._stop_event = threading.Event()  # Event để dừng sleep ngay lập tức

    def run(self):
        gpm = Gpm()
        profile_id = None
        
        status = None
        response_data = "-"
        try:
            # 1. Tạo Profile GPM mới
            if not self.is_running:
                status = "Đã dừng"
                return
            self.record.emit(self.index, "Đang tạo profile GPM", "-")
            if self.proxy:
                # Có proxy → dùng create_profile với proxy (format IP:PORT:USER:PASS hoặc IP:PORT)
                profile_id = gpm.create_profile(apiurl_Gpm=self.api_url_gpm, proxy=self.proxy)
                print(f"[Cảnh {self.index}] 🌐 Dùng proxy: {self.proxy}")
            else:
                # Không có proxy hoặc proxy sai định dạng → mở bằng proxy local
                profile_id = gpm.create_profile_2(apiurl_Gpm=self.api_url_gpm)
                print(f"[Cảnh {self.index}] 🏠 Dùng proxy local")
            
            # 2. Mở Profile GPM
            if not self.is_running:
                status = "Đã dừng"
                return
            self.record.emit(self.index, "Đang mở GPM", "-")
            remote_addr = gpm.open_profile(apiurl_Gpm=self.api_url_gpm, id_profile=profile_id)
            
            # Kiểm tra kết quả open_profile
            if not remote_addr:
                raise RuntimeError("open_profile trả về None — GPM chưa khởi động hoặc API URL GPM sai.")
            
            # 3. Kéo Playwright vào điều khiển trình duyệt
            if not self.is_running:
                status = "Đã dừng"
                return
            self.record.emit(self.index, "Đang chạy Playwright", "-")
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://{remote_addr}")
                context = browser.contexts[0]
                # Lấy tab mặc định hoặc tạo tab mới
                page = context.pages[0] if context.pages else context.new_page()
                
                # Mở google và nhập "test cảnh"
                page.goto("https://www.google.com")
                
                # Kiểm tra dừng sau khi goto
                if not self.is_running:
                    browser.close()
                    status = "Đã dừng"
                    return
                
                # Chọn thẻ input search của Google
                search_input = page.locator('textarea[name="q"], input[name="q"]').first
                search_input.wait_for(state="visible", timeout=15000)
                search_input.fill("test cảnh")
                page.keyboard.press("Enter")
                # Đợi một lát để trang load kết quả
                # page.wait_for_timeout(3000) 
                time.sleep(3)  # Dùng sleep thay vì wait_for để dễ dàng ngắt nếu dừng giữa chừng
                
                # Sau khi tìm kiếm xong, đánh dấu hoàn thành và đóng browser
                status = "Hoàn thành"
                print(f"[Cảnh {self.index}] ✅ Đã tìm kiếm xong, chuẩn bị đóng browser")
                time.sleep(8)  # Đợi thêm 8 giây trước khi đóng browser
                browser.close()
                
        except Exception as e:
            import traceback
            print(f"[Cảnh {self.index}] EXCEPTION:\n{traceback.format_exc()}")
            status = f"Lỗi: {e}"
            response_data = "-"
        finally:
            # 5. Dọn dẹp: Đóng và xóa profile GPM rác sau khi làm xong
            cleanup_success = True
            if profile_id:
                cleanup_success = self._cleanup_profile(gpm, profile_id)
            
            if status:
                final_status = status
                if status == "Hoàn thành" and not cleanup_success:
                    final_status = "Lỗi: Xóa profile GPM không thành công"
                    response_data = "-"
                self.record.emit(self.index, final_status, response_data)

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

        # Chờ GPM dừng profile và giải phóng tài nguyên trước khi xóa
        time.sleep(8)

        delete_attempts = 4
        for attempt in range(1, delete_attempts + 1):
            try:
                response = gpm.delete_profile(apiurl_Gpm=self.api_url_gpm, id_profile=profile_id)
                if isinstance(response, dict) and response.get("success") is False:
                    raise RuntimeError(f"delete_profile trả về lỗi: {response}")
                print(f"[Cảnh {self.index}] ✅ Đã delete_profile: {profile_id} (lần {attempt})")
                return True
            except Exception as e:
                print(f"[Cảnh {self.index}] ⚠️ delete_profile thất bại lần {attempt}: {e}")
                time.sleep(3 * attempt)
        print(f"[Cảnh {self.index}] ⚠️ Không xóa được profile {profile_id} sau {delete_attempts} lần.")
        return False

    def stop(self):
        """Ra hiệu dừng: đặt cả boolean lẫn Event để ngắt sleep ngay lập tức."""
        self.is_running = False
        self._stop_event.set()  # Ngắt bất kỳ _stop_event.wait() nào đang bị block

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

        # Kết nối sự kiện Click cho nút "BẮT ĐẦU TẠO VIDEO" bên tab Veo3
        self.veo3_btn_analyze.clicked.connect(self.startThreadVeo3)
        
        # Kết nối nút cập nhật phiên bản cho cả hai tab
        self.veo3_btn_update.clicked.connect(self._update_version)
        self.kol_btn_update.clicked.connect(self._update_version)

    def _set_veo3_btn_running(self, is_running):
        """Đổi trạng thái nút BẮT ĐẦU TẠO VIDEO theo trạng thái luồng."""
        if is_running:
            self.veo3_btn_analyze.setText("⏹  ĐANG CHẠY TẠO VIDEO...BẤM ĐỂ DỪNG CHẠY")
            self.veo3_btn_analyze.setStyleSheet(
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                " stop:0 #b91c1c, stop:1 #f97316);"
                " border: none; color: white; font-weight: bold;"
            )
        else:
            self.veo3_btn_analyze.setText("🚀  Bắt đầu tạo video từ tất cả cảnh")
            # Xoá style inline → fallback về QSS gốc (#analyzeBtn)
            self.veo3_btn_analyze.setStyleSheet("")

    def _stop_all_veo3_threads(self):
        """Dừng tất cả luồng đang chạy và reset UI ngay lập tức."""
        self._is_stopping = True  # Báo hiệu cho update_data bỏ qua signal từ giờ này
        for t in self.threads:
            t.stop()
        print("[Manager] ⛔ Đã gửi lệnh dừng tất cả luồng.")
        # Reset UI ngay lập tức — không chờ thread kết thúc
        self.running_threads = 0
        self.completed_threads = self.total_threads  # Đánh dấu đã xong để tránh lệch counter
        self.veo3_btn_running.setText("⏱ Đang xử lý 0 luồng")
        self._set_veo3_btn_running(False)
        QtWidgets.QApplication.processEvents()  # Buộc UI render ngay

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

        # Đọc danh sách proxy từ ô nhập thủ công trên giao diện
        # Giữ nguyên vị trí từng dòng: luồng i lấy proxy dòng i
        # Chỉ chấp nhận 2 dạng hợp lệ: IP:PORT:USER:PASS hoặc IP:PORT
        proxy_list = []
        proxy_text = self.te_proxy_input.toPlainText().strip()
        if proxy_text:
            for line in proxy_text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    proxy_list.append("")   # Dòng trống / comment → proxy local
                else:
                    parts = line.split(":")
                    if len(parts) == 2 or len(parts) == 4:
                        if all(parts):
                            proxy_list.append(line)   # Hợp lệ
                        else:
                            proxy_list.append("")
                            print(f"[Manager] ⚠️ Proxy sai định dạng (bỏ qua): '{line}'")
                    else:
                        proxy_list.append("")     # Sai định dạng → proxy local
                        print(f"[Manager] ⚠️ Proxy sai định dạng (bỏ qua): '{line}'")
            valid_count = sum(1 for p in proxy_list if p)
            print(f"[Manager] Đọc được {valid_count}/{len(proxy_list)} proxy hợp lệ. Proxy sai/không điền sẽ sử dụng proxy local.")

        self.total_threads = input_soluong
        self.completed_threads = 0
        self.running_threads = input_soluong  # Khởi đầu: tất cả luồng đều chạy
        
        # Cập nhật nút Đang xử lý: hiển thị số luồng đang chạy
        self.veo3_btn_running.setText(f"⏱ Đang xử lý {self.running_threads} luồng")

        # Đổi nút sang trạng thái đang chạy NGAY lập tức trước khi khởi động luồng
        self._set_veo3_btn_running(True)
        QtWidgets.QApplication.processEvents()  # Buộc UI render ngay

        self.threads = []

        # Khởi tạo GPM theo đúng số "cảnh" đã chọn
        for i in range(1, input_soluong + 1):
            # Lấy proxy theo index (dòng i-1 trong file), nếu không có thì dùng proxy local
            proxy = proxy_list[i - 1] if (i - 1) < len(proxy_list) else ""
            thread = MultiThread(
                index=i,
                api_url_gpm=input_api_url_gpm,
                proxy=proxy
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
            if status == "Hoàn thành" and response_data:
                # Cập nhật text prompt cho cảnh tương ứng nếu có dữ liệu trả về
                if 1 <= index <= len(self.scene_prompt_boxes):
                    self.scene_prompt_boxes[index - 1].setPlainText(response_data)

            self.completed_threads += 1
            self.running_threads = max(0, self.running_threads - 1)  # Giảm luồng đang chạy
            # Cập nhật nút hiển thị số luồng đang chạy
            self.veo3_btn_running.setText(f"⏱ Đang xử lý {self.running_threads} luồng")
            
            # Nếu tất cả các luồng đã xong (hoàn thành / lỗi / dừng)
            if self.completed_threads == self.total_threads:
                self.running_threads = 0
                self.veo3_btn_running.setText("⏱ Đang xử lý 0 luồng")
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
        """Thu gọn hoặc mở rộng bảng nhập proxy."""
        is_expanded = self.proxy_expand_panel.isVisible()
        if is_expanded:
            # Đóng panel → cập nhật text nút thu gọn
            self.proxy_expand_panel.setVisible(False)
            count = sum(
                1 for line in self.te_proxy_input.toPlainText().splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
            if count:
                self.btn_proxy_collapsed.setText(f"📋  {count} proxy — Nhấp để chỉnh sửa")
            else:
                self.btn_proxy_collapsed.setText("📋  Nhấp để nhập proxy...")
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
    window.resize(1920, 1080)
    window.show()
    sys.exit(app.exec_())