# -*- coding: utf-8 -*-
"""
Module hỗ trợ kiểm tra phiên bản và tự động cập nhật (auto-updater).
"""

import os
import sys
import time
import json
import requests
import subprocess
from PyQt5 import QtCore, QtWidgets

API_URL = "https://thangdz.com/api/categories/48576c9c-6da3-4690-885c-71b18ac49c3d"

def get_base_path():
    """Lấy thư mục gốc chứa ứng dụng."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def check_for_updates(current_version):
    """
    Kiểm tra phiên bản mới trực tiếp từ API.
    Trả về (has_update, new_version, update_url, error_msg)
    """
    try:
        # Gửi request GET tới API kiểm tra phiên bản
        response = requests.get(API_URL, params={"current_version": current_version}, timeout=10)
        print(f"[CheckUpdate] API response status code: {response.status_code}")
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as e:
                print(f"[CheckUpdate] JSON decode error: {e}, text: {response.text}")
                return False, current_version, None, f"Phản hồi từ API không phải JSON hợp lệ: {str(e)}"
                
            print(f"[CheckUpdate] API response data: {data}")
            new_version = data.get("version", "").strip()
            update_url = data.get("update_url", "").strip()
            if new_version and update_url:
                try:
                    curr_tuple = tuple(map(int, current_version.split('.')))
                    new_tuple = tuple(map(int, new_version.split('.')))
                    has_update = new_tuple > curr_tuple
                except Exception:
                    has_update = new_version != current_version
                
                print(f"[CheckUpdate] Current version: {current_version}, New version: {new_version}, Has update: {has_update}")
                return has_update, new_version, update_url, None
            else:
                return False, current_version, None, "API phản hồi thành công nhưng dữ liệu thiếu trường 'version' hoặc 'update_url'."
        else:
            print(f"[CheckUpdate] API response text: {response.text}")
            return False, current_version, None, f"Máy chủ trả về mã lỗi HTTP {response.status_code}."
    except requests.exceptions.RequestException as e:
        print(f"[CheckUpdate] Request exception: {e}")
        return False, current_version, None, f"Không thể kết nối tới máy chủ cập nhật: {str(e)}"
    except Exception as e:
        print(f"[CheckUpdate] Exception: {e}")
        return False, current_version, None, f"Đã xảy ra lỗi khi kiểm tra cập nhật: {str(e)}"


class DownloadWorker(QtCore.QThread):
    """Thread tải file chạy nền để tránh đơ UI."""
    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal(bool, str)

    def __init__(self, url, dest_path):
        super().__init__()
        self.url = url
        self.dest_path = dest_path

    def run(self):
        try:
            temp_dest = self.dest_path + ".tmp"
            response = requests.get(self.url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(temp_dest, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = int((downloaded / total_size) * 100)
                            self.progress.emit(percent)
            
            # Đổi tên file tạm thành file chính
            if os.path.exists(self.dest_path):
                try:
                    os.remove(self.dest_path)
                except Exception:
                    pass
            os.rename(temp_dest, self.dest_path)
            self.finished.emit(True, self.dest_path)
        except Exception as e:
            self.finished.emit(False, str(e))

def apply_update_windows(target_exe_path):
    """
    Thực hiện đè file exe cũ bằng file exe mới trên Windows.
    Sử dụng file script .bat chạy độc lập để xóa exe đang chạy sau khi app thoát.
    """
    base_path = get_base_path()
    # Tên của file exe mới đã được tải và đặt tên tạm bên cạnh file exe gốc
    new_exe_path = target_exe_path + ".new"
    
    # Tạo nội dung file bat cập nhật
    bat_path = os.path.join(base_path, "updater.bat")
    exe_name = os.path.basename(target_exe_path)
    
    bat_content = f"""@echo off
setlocal enabledelayedexpansion
title Tool Video AI Auto Updater

echo Dang cho ung dung dong hoan toan...
set /a count=0
:loop
tasklist /FI "IMAGENAME eq {exe_name}" 2>NUL | find /I "{exe_name}" >NUL
if !errorlevel! equ 0 (
    set /a count+=1
    if !count! lss 10 (
        timeout /t 1 /nobreak >nul
        goto loop
    )
    echo Khong the dong ung dung tu dong, dang thu cuong che...
    taskkill /F /IM "{exe_name}" >nul 2>&1
)

echo Dang tien hanh de file exe cap nhat...
del /f /q "{target_exe_path}"
move /y "{new_exe_path}" "{target_exe_path}"

if exist "{target_exe_path}" (
    echo Cap nhat thanh cong! Dang khoi chay lai ung dung...
    start "" "{target_exe_path}"
) else (
    echo Loi: Khong the di chuyen file cap nhat moi.
)

:: Xoa chinh file bat nay
del "%~f0"
"""
    
    with open(bat_path, "w", encoding="ansi") as f:
        f.write(bat_content)
        
    # Chạy file .bat ẩn danh/nền độc lập rồi tắt ứng dụng hiện tại
    subprocess.Popen([bat_path], shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
