from flask import Blueprint

from applications.auth.guard import ensure_logged_in
from applications.common.utils.http import success_api, fail_api

model_api = Blueprint('model_api', __name__, url_prefix='/api/model')


@model_api.before_request
def require_model_auth():
    return ensure_logged_in()


# HuggingFace 内置模型列表
HUGGINGFACE_MODELS = {
    "image_restoration": [
        {
            "model_path": "hf:caidas/swin2SR-classical-sr-x2-64",
            "model_type": "restorer",
            "model_name": "两倍细节增强",
            "backend": "huggingface",
            "description": "用途：2倍超分辨率重建。特点：基于Swin Transformer，能较好地恢复图像高频细节，适合轻微模糊图像。"
        },
        {
            "model_path": "hf:caidas/swin2SR-classical-sr-x4-64",
            "model_type": "restorer",
            "model_name": "四倍高清重建",
            "backend": "huggingface",
            "description": "用途：4倍超分辨率重建。特点：即使在放大倍数很高的情况下，仍保持较好的结构一致性，适合低分辨率图像。"
        }
    ],
    "object_detection": [
        {
            "model_path": "hf:facebook/detr-resnet-50",
            "model_type": "detector",
            "model_name": "全局上下文目标检测",
            "backend": "huggingface",
            "description": "用途：通用目标检测。特点：端到端Transformer架构，全局上下文理解能力强，适合检测大场景下的物体。"
        },
        {
            "model_path": "hf:microsoft/conditional-detr-resnet-50",
            "model_type": "detector",
            "model_name": "加速训练目标检测",
            "backend": "huggingface",
            "description": "用途：通用目标检测。特点：训练收敛速度比传统DETR快6.7倍，采用条件交叉注意力机制，定位更精准。"
        },
        {
            "model_path": "hf:StephanST/WALDO30",
            "model_type": "detector",
            "model_name": "航拍多目标识别",
            "backend": "huggingface",
            "description": "用途：航拍图像目标检测。支持类别：车辆、人员、建筑、船只、自行车、集装箱、卡车、油罐、挖掘机、太阳能板、公交等12类民用目标。"
        },
        {
            "model_path": "mmrotate:oriented_rcnn_r50_fpn_1x_dota_le90",
            "model_type": "detector",
            "model_name": "定向目标检测 (Oriented RCNN)",
            "backend": "mmrotate",
            "description": "用途：针对航拍图像中的旋转目标进行检测。特点：Oriented R-CNN 算法，DOTA 数据集训练，能够准确检测任意方向的密集排列物体。"
        }
    ],
    "semantic_segmentation": [
        {
            "model_path": "mmseg:cc-ln/CUGRS",
            "model_type": "segmenter",
            "model_name": "多要素地物分类",
            "backend": "mmsegmentation",
            "description": "用途：地物分类。支持类别：草地、林地、建筑、道路、裸地、水体。特点：结合DinoV3自监督特征和SwinTransformer，适合遥感复杂场景，泛化性强。"
        }
    ],
    "registration": [
        {
            "model_path": "hf:kornia/loftr",
            "model_type": "register",
            "model_name": "LoFTR 深度特征配准",
            "backend": "kornia",
            "description": "用途：多模态/大视角差异图像自动配准。特点：基于Transformer的局部特征匹配，无需检测关键点，对弱纹理和重复纹理鲁棒性强。"
        }
    ],
    "tracking": [
        {
            "model_path": "hf:opencv/csrt",
            "model_type": "tracker",
            "model_name": "CSRT 目标跟踪",
            "backend": "opencv",
            "description": "用途：单目标持续跟踪。特点：判别相关滤波器(DCF)与通道和空间可靠性(CSR)结合，适应目标形变和遮挡，精度较高。"
        }
    ]
}


def get_huggingface_models(model_type):
    """获取 HuggingFace 模型列表"""
    return HUGGINGFACE_MODELS.get(model_type, [])


@model_api.get('/list/<string:model_type>')
def get_model_list(model_type):
    types_list = {
        "change_detection": "change_detector",
        "classification": "classifier",
        "image_restoration": "restorer",
        "object_detection": "detector",
        "semantic_segmentation": "segmenter",
        "registration": "register",
        "tracking": "tracker"
    }
    if model_type not in types_list:
        return fail_api("模型类型不正确")
    
    expected_type = types_list[model_type]
    model_list = []

    hf_models = get_huggingface_models(model_type)
    model_list.extend(hf_models)
    
    return success_api(data=model_list)


@model_api.get('/huggingface/list')
def get_huggingface_model_list():
    """获取所有可用的 HuggingFace 模型"""
    return success_api(data=HUGGINGFACE_MODELS)
