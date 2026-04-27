from components.bunny_platform import Platform
import ctypes
import cv2
import numpy as np
import mss
from typing import List
import pygame.math


class PlatformDetector:
    def __init__(self, min_platform_width=100, color_diff_threshold=15, 
                 h_dilate_width=1, min_line_height=4):
        self.min_width = min_platform_width
        self.color_diff = color_diff_threshold
        self.h_dilate_width = h_dilate_width
        self.min_line_height = min_line_height
        self.sct = mss.mss()
        
    def capture_screen(self, monitor_index=1) -> np.ndarray:
        monitor = self.sct.monitors[monitor_index]
        screenshot = self.sct.grab(monitor)
        img = np.array(screenshot)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    
    def detect_platforms(self, image: np.ndarray, top_n: int = 5) -> List[Platform]:
        """返回 Platform 对象列表"""
        h, w = image.shape[:2]
        
        # 1. 灰度化
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # 2. CLAHE 对比度增强
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        
        # 3. 水平边缘检测
        scharr_y = cv2.Scharr(enhanced, cv2.CV_64F, 0, 1)
        scharr_y = np.abs(scharr_y)
        sobel_y = cv2.Sobel(enhanced, cv2.CV_64F, 0, 1, ksize=3)
        sobel_y = np.abs(sobel_y)
        combined = np.maximum(scharr_y, sobel_y)
        combined = np.uint8(combined * 255 / combined.max())
        
        # 4. 自适应二值化
        binary = cv2.adaptiveThreshold(
            np.uint8(combined), 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 15, -5
        )
        
        # 5. 纵向腐蚀
        v_kernel = np.ones((self.min_line_height, 1), np.uint8)
        v_eroded = cv2.erode(binary, v_kernel, iterations=1)
        
        # 6. 纵向膨胀恢复
        v_dilated = cv2.dilate(v_eroded, v_kernel, iterations=1)
        
        # 7. 水平膨胀
        h_kernel = np.ones((1, self.h_dilate_width), np.uint8)
        h_dilated = cv2.dilate(v_dilated, h_kernel, iterations=1)
        
        # 8. 行投影
        row_sums = np.sum(h_dilated > 0, axis=1)
        
        # 9. 找连续区域
        platforms_raw = []
        in_region = False
        region_start = 0
        
        for y in range(1, h):
            if row_sums[y] > self.min_width:
                if not in_region:
                    region_start = y
                    in_region = True
            else:
                if in_region:
                    region_height = y - region_start
                    if self.min_line_height <= region_height <= 40:
                        platforms_raw.append((0, region_start, w, region_height))
                    in_region = False
        
        # 10. 细化边界
        refined = []
        
        for _, py, _, ph in platforms_raw:
            roi = h_dilated[py:py+ph, :]
            contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                x, y, cw, ch = cv2.boundingRect(contour)
                if cw >= self.min_width:
                    platform_top = py + y + ch
                    thickness = max(ch, 2)
                    refined.append((x, platform_top, cw, thickness))
        
        # 11. 合并
        merged_rects = self._merge_platforms(refined)
        
        # 12. ✅ 按长度降序筛选 top_n
        merged_rects = self._filter_top_by_screen_center(merged_rects, top_n=top_n)
        
        # 转换为 Platform 对象
        platforms = []
        for x, y, w_rect, h_rect in merged_rects:
            vertex = pygame.math.Vector2(x, y - h_rect)
            size = pygame.math.Vector2(w_rect, h_rect)
            platforms.append(Platform(vertex, size))
        
        return platforms
    
    def _merge_platforms(self, rects):
        if len(rects) <= 1:
            return rects
        rects.sort(key=lambda r: (r[1], r[0]))
        merged = []
        used = [False] * len(rects)
        
        for i, (x1, y1, w1, h1) in enumerate(rects):
            if used[i]:
                continue
            current = [x1, y1, x1 + w1, y1]
            for j in range(i + 1, len(rects)):
                if used[j]:
                    continue
                x2, y2, w2, h2 = rects[j]
                if abs(y1 - y2) < 10 and not (current[2] < x2 or x2 + w2 < current[0]):
                    current[0] = min(current[0], x2)
                    current[2] = max(current[2], x2 + w2)
                    used[j] = True
            merged.append((current[0], current[1], current[2] - current[0], h1))
            used[i] = True
        return merged
    
    def _filter_top_by_screen_center(self, platforms, top_n=5):
        """挑选 y 最接近屏幕高度一半的前 top_n 个平台"""
        if len(platforms) <= top_n:
            return platforms
        
        # 获取屏幕工作区高度
        class RECT(ctypes.Structure):
            _fields_ = [
                ('left', ctypes.c_long),
                ('top', ctypes.c_long),
                ('right', ctypes.c_long),
                ('bottom', ctypes.c_long),
            ]
        
        rect = RECT()
        SPI_GETWORKAREA = 48
        ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
        screen_center_y = (rect.bottom - rect.top) / 2
        
        platforms.sort(key=lambda r: abs(r[1] - screen_center_y))
        return platforms[:top_n]
    
    def get_platforms_for_bunny(self, top_n: int = 5) -> List[Platform]:
        """截图并返回 Platform 对象列表"""
        img = self.capture_screen()
        return self.detect_platforms(img, top_n=top_n)
    
    def test_visualize(self, save_prefix="test"):
        """测试可视化"""
        img = self.capture_screen()
        cv2.imwrite(f"{save_prefix}_0_原图.png", img)
        
        platforms = self.detect_platforms(img, top_n=5)
        
        # 画结果
        result = img.copy()
        for p in platforms:
            x = int(p.vertex.x)
            y = int(p.vertex.y)
            w = int(p.size.x)
            h = int(p.size.y)
            cv2.rectangle(result, (x, y), (x + w, y + h), (0, 255, 0), 2)
        
        cv2.line(result, (0, img.shape[0] // 2), (img.shape[1], img.shape[0] // 2), (255, 0, 0), 1)
        cv2.imwrite(f"{save_prefix}_最终结果.png", result)
        
        print(f"\n共检测到 {len(platforms)} 个平台:")
        for i, p in enumerate(platforms):
            print(f"  平台{i+1}: x={p.vertex.x:.0f}, y={p.vertex.y:.0f}, "
                  f"w={p.size.x:.0f}, h={p.size.y:.0f}")


if __name__ == "__main__":
    detector = PlatformDetector(
        min_platform_width=100,
        color_diff_threshold=15,
        h_dilate_width=1,
        min_line_height=2
    )
    detector.test_visualize("test")
