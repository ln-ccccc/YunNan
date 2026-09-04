def get_mmseg_model_id(model_path: str) -> str:
    if model_path.startswith("mmseg:"):
        return model_path[6:]
    return model_path


def execute_mmseg(model_path, data_path, out_dir, test_names):
    from applications.interface import mmseg_inference_caller

    model_id = get_mmseg_model_id(model_path)
    return mmseg_inference_caller.execute(
        model_id=model_id,
        data_path=data_path,
        out_dir=out_dir,
        names=test_names,
    )


def execute(model_path, data_path, out_dir, test_names):
    model_id = get_mmseg_model_id(model_path)
    print(f"[SemanticSegmentation] 使用 MMSegmentation 模型: {model_id}", flush=True)
    return execute_mmseg(model_path, data_path, out_dir, test_names)
