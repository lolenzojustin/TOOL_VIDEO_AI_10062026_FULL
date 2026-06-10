# -*- coding: utf-8 -*-
"""
Module giao diện nhập License Key.
Người dùng bắt buộc phải nhập đúng license key còn hạn mới vào được giao diện tool.
Import vào file chạy chính:
    from license_key_dialog import LicenseKeyDialog
Sử dụng:
    dialog = LicenseKeyDialog()
    if not dialog.exec_accepted():
        sys.exit(0)
"""

from PyQt5 import QtCore, QtGui, QtWidgets
import sys
import os
import json
import requests
import socket
import platform
import subprocess
import hashlib


class LicenseKeyDialog(QtWidgets.QDialog):
    """Dialog yêu cầu người dùng nhập License Key trước khi vào tool."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Xác thực License Key")
        self.setFixedSize(520, 420)
        self.setWindowFlags(
            QtCore.Qt.Dialog
            | QtCore.Qt.WindowCloseButtonHint
            | QtCore.Qt.MSWindowsFixedSizeDialogHint
        )
        self._accepted = False
        
        # Cấu hình API và thông tin phần cứng
        self.category = "Tool_video_AI"
        self._init_paths()
        self.device_id = self._get_device_id()
        self.device_name = self._get_device_name()
        self.os_info = self._get_os_info()
        self.app_version = self._get_app_version()

        self._build_ui()
        self._apply_styles()

    def _init_paths(self):
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            internal_dir = os.path.join(exe_dir, '_internal')
            self.base_path = internal_dir if os.path.exists(os.path.join(internal_dir, 'config.env')) else exe_dir
        else:
            self.base_path = os.path.dirname(os.path.abspath(__file__))
        self.license_path = os.path.join(self.base_path, "license.json")

    def _get_device_id(self):
        device_str = ""
        # 1. Thử lấy Motherboard UUID qua WMIC
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
            out = subprocess.check_output(
                "wmic csproduct get uuid", 
                startupinfo=startupinfo,
                stderr=subprocess.DEVNULL,
                shell=True
            ).decode().split()
            for item in out:
                item_clean = item.strip()
                if item_clean and "UUID" not in item_clean and "uuid" not in item_clean:
                    if "FFFFFFFF" not in item_clean and "00000000" not in item_clean:
                        device_str = item_clean
                        break
        except Exception:
            pass
            
        # 2. Thử lấy MachineGuid từ Registry nếu cách 1 thất bại
        if not device_str:
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                winreg.CloseKey(key)
                if guid:
                    device_str = guid.strip()
            except Exception:
                pass
                
        # 3. Thử lấy MAC address qua uuid.getnode nếu vẫn thất bại
        if not device_str:
            try:
                import uuid
                mac = uuid.getnode()
                if mac:
                    device_str = f"MAC-{mac}"
            except Exception:
                pass
                
        if not device_str:
            device_str = "UNKNOWN-DEVICE-ID"

        # Băm thành chuỗi cố định đẹp và an toàn
        return hashlib.sha256(device_str.encode('utf-8')).hexdigest()

    def _get_device_name(self):
        try:
            return socket.gethostname()
        except Exception:
            try:
                return platform.node()
            except Exception:
                return "UnknownPC"

    def _get_os_info(self):
        try:
            return f"{platform.system()} {platform.release()} (v{platform.version()})"
        except Exception:
            return "Windows"

    def _get_app_version(self):
        try:
            config_path = os.path.join(self.base_path, "config.env")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("VERSION="):
                            return line.split("=", 1)[1].strip()
        except Exception:
            pass
        return "1.0"

    def _save_license_key(self, key):
        try:
            with open(self.license_path, "w", encoding="utf-8") as f:
                json.dump({"license_key": key}, f, indent=4)
        except Exception as e:
            print(f"Lỗi lưu file license: {e}")

    def _load_saved_license_key(self):
        try:
            if os.path.exists(self.license_path):
                with open(self.license_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("license_key", "").strip()
        except Exception:
            pass
        return ""

    def _activate_license_api(self, key):
        """
        Gọi API active License Key.
        Trả về (success: bool, message: str)
        """
        url = "http://localhost:8000/api/activation/activate"
        payload = {
            "license_key": key,
            "device_id": self.device_id,
            "category": self.category,
            "device_name": self.device_name,
            "os_info": self.os_info,
            "app_version": self.app_version
        }
        headers = {
            'Content-Type': 'application/json'
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            try:
                res_data = response.json()
            except Exception:
                res_data = {}

            # Chỉ chấp nhận khi response JSON có status == "valid"
            if isinstance(res_data, dict) and res_data.get("status") == "valid":
                return True, "Kích hoạt bản quyền thành công!"
            else:
                msg = ""
                if isinstance(res_data, dict):
                    msg = res_data.get("message") or res_data.get("error") or res_data.get("detail")
                if not msg:
                    status_val = res_data.get("status", "") if isinstance(res_data, dict) else ""
                    msg = f"License Key không hợp lệ (status: {status_val})" if status_val else f"Mã lỗi HTTP: {response.status_code}"
                return False, f"Lỗi kích hoạt: {msg}"
        except requests.exceptions.RequestException as e:
            return False, f"Không thể kết nối máy chủ: {str(e)}"

    def _check_license_api(self, key):
        """
        Gọi API check License Key.
        Trả về (success: bool, status: str)
        """
        url = "http://localhost:8000/api/activation/check"
        payload = {
            "license_key": key,
            "device_id": self.device_id,
            "category": self.category
        }
        headers = {
            'Content-Type': 'application/json'
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            try:
                res_data = response.json()
            except Exception:
                res_data = {}

            # Chỉ chấp nhận khi response JSON có status == "valid"
            if isinstance(res_data, dict) and res_data.get("status") == "valid":
                return True, "valid"
            else:
                return False, "invalid"
        except requests.exceptions.RequestException:
            return False, "network"

    # ──────────────────────────────────────────────
    # Xây dựng giao diện
    # ──────────────────────────────────────────────
    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Container chính ──
        container = QtWidgets.QFrame()
        container.setObjectName("licenseContainer")
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(40, 30, 40, 30)
        container_layout.setSpacing(0)

        # ── Icon khoá ──
        icon_label = QtWidgets.QLabel("🔐")
        icon_label.setObjectName("licenseIcon")
        icon_label.setAlignment(QtCore.Qt.AlignCenter)
        container_layout.addWidget(icon_label)

        container_layout.addSpacing(10)

        # ── Tiêu đề ──
        title = QtWidgets.QLabel("XÁC THỰC BẢN QUYỀN")
        title.setObjectName("licenseTitle")
        title.setAlignment(QtCore.Qt.AlignCenter)
        container_layout.addWidget(title)

        container_layout.addSpacing(6)

        # ── Mô tả ──
        subtitle = QtWidgets.QLabel(
            "Vui lòng nhập License Key để kích hoạt và sử dụng tool.\n"
            "Liên hệ Admin nếu bạn chưa có key bản quyền."
        )
        subtitle.setObjectName("licenseSubtitle")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setWordWrap(True)
        container_layout.addWidget(subtitle)

        container_layout.addSpacing(24)

        # ── Label ô nhập ──
        input_label = QtWidgets.QLabel("LICENSE KEY")
        input_label.setObjectName("inputLabel")
        container_layout.addWidget(input_label)

        container_layout.addSpacing(6)

        # ── Ô nhập license key ──
        self.le_license = QtWidgets.QLineEdit()
        self.le_license.setObjectName("licenseInput")
        self.le_license.setPlaceholderText("Nhập license key tại đây...")
        self.le_license.setFixedHeight(46)
        self.le_license.setEchoMode(QtWidgets.QLineEdit.Password)
        container_layout.addWidget(self.le_license)

        container_layout.addSpacing(8)

        # ── Checkbox hiện/ẩn key ──
        self.cb_show = QtWidgets.QCheckBox("Hiển thị License Key")
        self.cb_show.setObjectName("showKeyCheckbox")
        self.cb_show.setCursor(QtCore.Qt.PointingHandCursor)
        self.cb_show.toggled.connect(self._toggle_echo)
        container_layout.addWidget(self.cb_show)

        container_layout.addSpacing(16)

        # ── Thông báo lỗi (ẩn mặc định) ──
        self.lb_error = QtWidgets.QLabel("")
        self.lb_error.setObjectName("licenseError")
        self.lb_error.setAlignment(QtCore.Qt.AlignCenter)
        self.lb_error.setWordWrap(True)
        self.lb_error.setVisible(False)
        container_layout.addWidget(self.lb_error)

        container_layout.addSpacing(10)

        # ── Nút kích hoạt ──
        self.btn_activate = QtWidgets.QPushButton("🔓  KÍCH HOẠT BẢN QUYỀN")
        self.btn_activate.setObjectName("activateBtn")
        self.btn_activate.setFixedHeight(48)
        self.btn_activate.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_activate.clicked.connect(self._on_activate)
        container_layout.addWidget(self.btn_activate)

        container_layout.addStretch()

        # ── Footer ──
        footer = QtWidgets.QLabel("© 2026 Video AI Tool — All rights reserved")
        footer.setObjectName("licenseFooter")
        footer.setAlignment(QtCore.Qt.AlignCenter)
        container_layout.addWidget(footer)

        main_layout.addWidget(container)

        # Enter để kích hoạt
        self.le_license.returnPressed.connect(self._on_activate)

    # ──────────────────────────────────────────────
    # Xử lý sự kiện
    # ──────────────────────────────────────────────
    def _toggle_echo(self, checked):
        if checked:
            self.le_license.setEchoMode(QtWidgets.QLineEdit.Normal)
        else:
            self.le_license.setEchoMode(QtWidgets.QLineEdit.Password)

    def _on_activate(self):
        key = self.le_license.text().strip()
        if not key:
            self._show_error("⚠️  Vui lòng nhập License Key!")
            return
        
        # Đổi trạng thái nút kích hoạt để báo đang chạy
        self.btn_activate.setEnabled(False)
        self.btn_activate.setText("⏳  ĐANG KÍCH HOẠT BẢN QUYỀN...")
        QtWidgets.QApplication.processEvents() # Cập nhật UI ngay lập tức
        
        print(f"[License] Đang kích hoạt License Key: {key}...")
        sys.stdout.flush()
        success, msg = self._activate_license_api(key)
        
        if success:
            print("[License] Kích hoạt bản quyền thành công!")
            sys.stdout.flush()
            self._save_license_key(key)
            self._accepted = True
            self.accept()
        else:
            print(f"[License] Kích hoạt bản quyền thất bại! (Lỗi: {msg})")
            sys.stdout.flush()
            self._show_error(f"❌  {msg}")
            self.le_license.selectAll()
            self.le_license.setFocus()
            self.btn_activate.setEnabled(True)
            self.btn_activate.setText("🔓  KÍCH HOẠT BẢN QUYỀN")

    def _show_error(self, msg, shake=True):
        self.lb_error.setText(msg)
        self.lb_error.setVisible(True)
        if shake:
            # Hiệu ứng rung nhẹ
            self._shake_animation()

    def _shake_animation(self):
        """Hiệu ứng rung nhẹ khi nhập sai."""
        anim = QtCore.QPropertyAnimation(self, b"pos")
        anim.setDuration(300)
        pos = self.pos()
        anim.setKeyValueAt(0, pos)
        anim.setKeyValueAt(0.1, pos + QtCore.QPoint(8, 0))
        anim.setKeyValueAt(0.2, pos + QtCore.QPoint(-8, 0))
        anim.setKeyValueAt(0.3, pos + QtCore.QPoint(6, 0))
        anim.setKeyValueAt(0.4, pos + QtCore.QPoint(-6, 0))
        anim.setKeyValueAt(0.5, pos + QtCore.QPoint(4, 0))
        anim.setKeyValueAt(0.6, pos + QtCore.QPoint(-4, 0))
        anim.setKeyValueAt(0.7, pos + QtCore.QPoint(2, 0))
        anim.setKeyValueAt(0.8, pos + QtCore.QPoint(-2, 0))
        anim.setKeyValueAt(1.0, pos)
        anim.start()
        # Giữ tham chiếu để animation không bị GC xoá
        self._anim = anim

    def exec_accepted(self):
        """Chạy dialog và trả về True nếu key hợp lệ, False nếu đóng/thoát."""
        saved_key = self._load_saved_license_key()
        if saved_key:
            print(f"[License] Phát hiện key lưu sẵn: {saved_key}. Đang xác thực...")
            sys.stdout.flush()
            is_valid, status = self._check_license_api(saved_key)
            if is_valid:
                print("[License] Xác thực tự động thành công!")
                sys.stdout.flush()
                self._accepted = True
                return True
            else:
                print(f"[License] Xác thực tự động thất bại! (Trạng thái: {status})")
                sys.stdout.flush()
                self.le_license.setText(saved_key)
                if status == "network":
                    self._show_error("⚠️  Không thể kết nối máy chủ để kiểm tra bản quyền.\nVui lòng kiểm tra kết nối mạng.", shake=False)
                else:
                    self._show_error("❌  License Key đã lưu không hợp lệ hoặc đã hết hạn!\nVui lòng kích hoạt lại.", shake=False)
                
        result = self.exec_()
        return self._accepted and result == QtWidgets.QDialog.Accepted

    def closeEvent(self, event):
        """Khi người dùng bấm X đóng dialog thì không cho vào tool."""
        if not self._accepted:
            event.accept()
        else:
            event.accept()

    # ──────────────────────────────────────────────
    # Giao diện CSS (phong cách đồng bộ với tool chính)
    # ──────────────────────────────────────────────
    def _apply_styles(self):
        self.setStyleSheet("""
            /* ── Container chính ── */
            #licenseContainer {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #07111f, stop:0.5 #0a1628, stop:1 #07111f
                );
                border: 1px solid #1e3a5f;
                border-radius: 12px;
            }

            /* ── Icon ── */
            #licenseIcon {
                font-size: 52px;
                background: transparent;
                border: none;
            }

            /* ── Tiêu đề ── */
            #licenseTitle {
                color: #ffffff;
                font-size: 22px;
                font-weight: bold;
                letter-spacing: 3px;
                background: transparent;
                border: none;
            }

            /* ── Phụ đề ── */
            #licenseSubtitle {
                color: #94a3b8;
                font-size: 12px;
                line-height: 1.5;
                background: transparent;
                border: none;
            }

            /* ── Label ô nhập ── */
            #inputLabel {
                color: #7c3aed;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 2px;
                background: transparent;
                border: none;
            }

            /* ── Ô nhập license key ── */
            #licenseInput {
                background-color: #111827;
                color: #e5e7eb;
                border: 2px solid #1e3a5f;
                border-radius: 8px;
                padding: 0 16px;
                font-size: 15px;
                font-weight: bold;
                letter-spacing: 1px;
            }
            #licenseInput:focus {
                border: 2px solid #7c3aed;
                background-color: #0f172a;
            }
            #licenseInput::placeholder {
                color: #4b5563;
                font-weight: normal;
                letter-spacing: 0px;
            }

            /* ── Checkbox hiện key ── */
            #showKeyCheckbox {
                color: #64748b;
                font-size: 11px;
                background: transparent;
                border: none;
                spacing: 6px;
            }
            #showKeyCheckbox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #475569;
                border-radius: 3px;
                background: #111827;
            }
            #showKeyCheckbox::indicator:checked {
                background: #7c3aed;
                border: 1px solid #7c3aed;
            }

            /* ── Thông báo lỗi ── */
            #licenseError {
                color: #ef4444;
                font-size: 12px;
                font-weight: bold;
                background: #1c0a0a;
                border: 1px solid #7f1d1d;
                border-radius: 6px;
                padding: 8px 12px;
            }

            /* ── Nút kích hoạt ── */
            #activateBtn {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #7c3aed, stop:1 #a855f7
                );
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 1px;
                border: none;
                border-radius: 8px;
            }
            #activateBtn:hover {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6d28d9, stop:1 #9333ea
                );
            }
            #activateBtn:pressed {
                background: #5b21b6;
            }

            /* ── Footer ── */
            #licenseFooter {
                color: #334155;
                font-size: 10px;
                background: transparent;
                border: none;
            }
        """)
