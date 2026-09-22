from flask import Flask

from .flask_uploads import UploadSet, IMAGES
from .flask_uploads import configure_uploads

# S1 格式扩展：IMG(ERDAS)/ENVI(.dat/.bin+.hdr 成对)/JP2 与 tif 一样直读；
# jpg/png/gif 沿用 IMAGES（非地理遗留通道）
IMAGES_WITH_TIFF = IMAGES + ('tif', 'tiff', 'img', 'dat', 'bin', 'hdr', 'jp2')
photos = UploadSet('photos', IMAGES_WITH_TIFF)


def init_upload(app: Flask):
    configure_uploads(app, photos)
