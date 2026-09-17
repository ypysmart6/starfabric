# 地球影像来源

以下图片原样保存在工程内，由本地前端提供，不依赖浏览器访问外网。

| 文件 | 来源 | 用途 |
|---|---|---|
| `earth-day.png` | [NASA SVS Blue Marble](https://svs.gsfc.nasa.gov/2915/) · [原始 PNG](https://svs.gsfc.nasa.gov/vis/a000000/a002900/a002915/bluemarble-2048.png) | 2048 × 1024 经纬度地表贴图 |
| `earth-night.png` | [NASA SVS Earth at Night](https://svs.gsfc.nasa.gov/2916/) · [原始 PNG](https://svs.gsfc.nasa.gov/vis/a000000/a002900/a002916/earthatnight-2048.png) | 2048 × 1024 夜间城市灯光 |

Blue Marble credit: NASA/Goddard Space Flight Center Scientific Visualization Studio. Blue Marble Next Generation: Reto Stockli (NASA/GSFC), NASA's Earth Observatory.

Earth at Night: 数据来自 Marc Imhoff (NASA/GSFC) 和 Christopher Elvidge (NOAA/NGDC)；图像由 Craig Mayhew (NASA/GSFC) 和 Robert Simmon (NASA/GSFC) 制作。

这些是历史观测合成图，不是实时地表、天气或用电观测。地表随模型的 GMST 转动；昼夜明暗来自模型时刻的太阳方向。太阳圆盘、大气光晕和背景星点为程序绘制的视觉元素，背景不是天文星图。

太阳近似算法来源：[US Naval Observatory — Computing Approximate Solar Coordinates](https://aa.usno.navy.mil/faq/sun_approx)。本界面将日期赤道坐标方向近似用于 TEME 视图，只用于态势展示，不参与轨道传播、遮挡验算或供电计算。太阳距离与大小经过显示压缩，AU 读数保留算法计算的日地距离。
