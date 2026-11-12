@echo off
setlocal
title Nethra - Repair and Launch

cd /d "%~dp0"

set "PYTHON=C:\Users\justn\AppData\Local\Programs\Python\Python314\python.exe"
set "APP=%~dp0Nethra_Fixed.py"

echo.
echo ==========================================
echo       NETHRA - CHECK AND LAUNCH
echo ==========================================
echo.

if not exist "%PYTHON%" (
    echo ERROR: Python 3.14 was not found at:
    echo %PYTHON%
    echo.
    echo Update the PYTHON path in this BAT file.
    pause
    exit /b 1
)

if not exist "%APP%" (
    echo ERROR: Nethra_Fixed.py is not in this folder:
    echo %~dp0
    echo Keep this BAT file beside Nethra_Fixed.py.
    pause
    exit /b 1
)

echo Checking OpenCV with the same Python used by VS Code...
"%PYTHON%" -c "import cv2; print('OpenCV:', cv2.__version__); print('CascadeClassifier:', hasattr(cv2, 'CascadeClassifier')); raise SystemExit(0 if hasattr(cv2, 'CascadeClassifier') else 1)"

if errorlevel 1 (
    echo.
    echo OpenCV is missing or conflicting. Repairing installation...
    "%PYTHON%" -m pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless opencv-contrib-python-headless

    if errorlevel 1 (
        echo.
        echo Could not remove the old OpenCV packages.
        pause
        exit /b 1
    )

    "%PYTHON%" -m pip install --no-cache-dir opencv-python==4.13.0.92 pillow pyttsx3

    if errorlevel 1 (
        echo.
        echo Package installation failed. Check your internet connection.
        pause
        exit /b 1
    )
)

echo.
echo Verifying camera and face-detection support...
"%PYTHON%" -c "import cv2; from PIL import Image, ImageTk; print('OpenCV:', cv2.__version__); print('Face detection:', hasattr(cv2, 'CascadeClassifier')); print('Camera API:', hasattr(cv2, 'VideoCapture')); print('Haar files:', getattr(getattr(cv2, 'data', None), 'haarcascades', 'not found')); raise SystemExit(0 if hasattr(cv2, 'CascadeClassifier') and hasattr(cv2, 'VideoCapture') else 1)"

if errorlevel 1 (
    echo.
    echo Nethra cannot start because a required library check failed.
    pause
    exit /b 1
)

echo.
echo Launching Nethra...
echo.
"%PYTHON%" "%APP%"

if errorlevel 1 (
    echo.
    echo Nethra closed with an error. Check the message above.
    pause
)

endlocal
