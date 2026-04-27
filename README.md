# Desktop Bunny

![Bunny](assets/icon.png)

## Quick Start
安装Python版本 3.13.13

> pip install -r requirements.txt
>
> pyinstaller --onefile --windowed --icon=assets/icon.png --add-data "assets\*;assets" --add-data "components\*.py;components" main.py

运行`dist`目录下的`main.exe`