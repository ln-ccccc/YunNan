import os.path as osp

import yaml
import logging


def get_model_info(model_dir):
    if not osp.exists(model_dir):
        logging.getLogger(__name__).error("Directory '%s' does not exist!", model_dir)
    if not osp.exists(osp.join(model_dir, "model.yml")):
        raise FileNotFoundError(
            "There is no file named model.yml in {}.".format(model_dir))
    with open(osp.join(model_dir, "model.yml")) as f:
        model_info = yaml.load(f.read(), Loader=yaml.Loader)
    return model_info
