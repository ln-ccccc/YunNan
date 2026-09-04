from typing import Dict, List, Tuple


def run_mmseg_tiles(
    *,
    model_id: str,
    data_path: str,
    out_dir: str,
    file_names: List[str],
    device: str,
    caller=None,
) -> Tuple[List[str], Dict[str, str]]:
    if caller is None:
        from applications.interface import mmseg_inference_caller

        caller = mmseg_inference_caller

    failed_tiles: List[str] = []
    tile_errors: Dict[str, str] = {}

    try:
        details = caller.execute(
            model_id=model_id,
            data_path=data_path,
            out_dir=out_dir,
            names=file_names,
            device=device,
            return_details=True,
        )
    except Exception as e:
        return list(file_names), {tile_name: str(e) for tile_name in file_names}

    result_map = {result.get("input_name"): result for result in details.get("results", [])}
    for tile_name in file_names:
        result = result_map.get(tile_name)
        if result is None:
            failed_tiles.append(tile_name)
            tile_errors[tile_name] = "推理进程未返回该瓦片结果"
        elif result.get("status") != "success":
            failed_tiles.append(tile_name)
            tile_errors[tile_name] = result.get("error") or "未知推理错误"

    return failed_tiles, tile_errors

