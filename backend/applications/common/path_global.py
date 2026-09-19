import hashlib
import random

# 上传文件地址
up_dir = "static/upload/"
# 生成的文件地址
generate_dir = "static/upload/res/"
# 网络地址
generate_url = "/_uploads/photos/res/"
up_url = "/_uploads/photos/"


def md5_name(name):
    nname = hashlib.md5(str(random.random()).encode()).hexdigest() + "_" + name
    if len(nname) > 100:
        # 无扩展名输入显式拒绝（split(".")[1] 会 IndexError）；多段扩展名取最后一段
        parts = name.rsplit(".", 1)
        if len(parts) != 2 or not parts[1]:
            raise ValueError("文件名过长且缺少可用的扩展名")
        nname = hashlib.md5(str(random.random()).encode()).hexdigest() + "." + parts[1]
    return nname


"""
 1=直方图匹配，
 2=对比度自适应直方图均衡化(CLAHE)，
 3=平滑(中值滤波)，
 4=锐化，
 5=高斯滤波
 6=变化检测渲染，
 7=目标提取渲染，
 8=孔洞填充(用于变化检测结果图处理)
"""
fun_type_1 = 1
fun_type_2 = 2
fun_type_3 = 3
fun_type_4 = 4
fun_type_5 = 5
fun_type_6 = 6
fun_type_7 = 7
fun_type_8 = 8
