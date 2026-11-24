# -*- coding: utf-8 -*-
"""物理单位换算：当地的「每度多少米」，以及 label 栅格一个像元的真米尺寸。

生产成果的 label 有两种坐标系（3029 条里 2930 条 EPSG:4326、99 条 EPSG:3857）：同样写着一个
像元尺寸，有的单位是度，有的是带墨卡托放大的「米」。这里不按坐标系分支手写换算公式，统一交给
PROJ（rasterio 自带；生产镜像里没有 pyproj）：把点变换到以本处为中心的等距方位投影，在投影
平面里按米量长度——等距方位投影从中心出发的径向距离等于椭球面上的真实距离。

只做单位换算，不做任何几何判定。数值核对：与独立的子午圈 / 卯酉圈级数公式一致到 1e-7。
"""
import math

from rasterio.warp import transform

WGS84 = "EPSG:4326"
# 量「每度多少米」用的步长（度），约 11 米。太大就成了这一段弧的平均值而不是该点的值
DEGREE_SAMPLE = 1e-4


def _centred_azimuthal_equidistant(longitude, latitude):
    return f"+proj=aeqd +lat_0={latitude:.10f} +lon_0={longitude:.10f} +datum=WGS84 +units=m +no_defs"


def metres_per_degree(longitude, latitude):
    """该经纬度处，经向与纬向各一度是多少米。返回 (经向, 纬向)。"""
    target = _centred_azimuthal_equidistant(longitude, latitude)
    xs, ys = transform(
        WGS84, target,
        [longitude, longitude + DEGREE_SAMPLE, longitude],
        [latitude, latitude, latitude + DEGREE_SAMPLE],
    )
    return xs[1] / DEGREE_SAMPLE, ys[2] / DEGREE_SAMPLE


def pixel_size_metres(dataset):
    """栅格数据集一个像元的真米尺寸 (x 向, y 向)。坐标系未知或量不出来时返回 None。

    只读栅格的头信息（仿射变换、坐标系、范围），不读像元。
    """
    if dataset.crs is None:
        return None
    affine = dataset.transform
    left, bottom, right, top = dataset.bounds
    centre_x, centre_y = (left + right) / 2, (bottom + top) / 2
    lon, lat = transform(dataset.crs, WGS84, [centre_x], [centre_y])
    xs, ys = transform(
        dataset.crs, _centred_azimuthal_equidistant(lon[0], lat[0]),
        [centre_x, centre_x + affine.a, centre_x + affine.b],
        [centre_y, centre_y + affine.d, centre_y + affine.e],
    )
    size = (
        math.hypot(xs[1] - xs[0], ys[1] - ys[0]),
        math.hypot(xs[2] - xs[0], ys[2] - ys[0]),
    )
    if not all(math.isfinite(value) and value > 0 for value in size):
        return None
    return size
