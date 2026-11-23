//https://blog.csdn.net/tattoo_jie/article/details/122251905压缩打包功能
import JSZIP from "jszip"
import FileSaver from 'file-saver'

import global from '@/global'
import {hideFullScreenLoading} from "@/utils/loading";
import { saveBlobAsFile } from "@/utils/blobDownload.mjs";
function downloadimgWithWords(index, src, funtype) {
  // 凭证必带：图片资源在 :5008（/_uploads 全部要求登录），页面在 :3000 跨源，
  // fetch 默认 same-origin 不带 Cookie 会 401（2026-09-20 审查 P1 回归修复）
  fetch(src, { credentials: "include" })
    .then((response) => {
      if (!response.ok) throw new Error(`下载失败: ${response.status}`);
      return response.blob();
    })//链式编程
    .then((res) => {
      // 经公共函数触发下载：object URL 由它延迟释放（同步释放会截断尚未开始的下载）
      saveBlobAsFile(new Blob([res]), index === -1 ? `${funtype}` : `第${index}组${funtype}`);
    })
    .catch((error) => {
      // 失败时留在控制台留痕即可，不能把错误页面内容当图片下载
      console.error('下载图片失败:', error);
    });
}
function getImgArrayBuffer(url) {
  return new Promise((resolve, reject) => {
    //通过请求获取文件blob格式
    let xmlhttp = new XMLHttpRequest();
    xmlhttp.open("GET",global.BASEURL+url, true);
    // 跨源下载必须带 Cookie（/_uploads 要求登录），默认 false 会 401
    xmlhttp.withCredentials = true;
    xmlhttp.responseType = "blob";
    xmlhttp.onload = function () {
      if (this.status === 200) {
        resolve(this.response);
      } else {
        reject(this.status);
      }
    };
    xmlhttp.send();
  });
}
//批量下载
function atchDownload(compressImg) {
  // this.images 是要下载的图片数组  [{url: 图片地址, id: 图片名称}]
  // 定时器 loading

  let _this = this;
  let zip = new JSZIP();
  let cache = {};
  let promises = [];
  for (let item of compressImg) {
    const promise = _this.getImgArrayBuffer(item.after_img).then((data) => {    //获取单个文件的promise返回
      // 下载文件, 并存成ArrayBuffer对象(blob)
      zip.file(item.id + ".png", data, { binary: true }); // 逐个添加文件
      cache[item.id] = data;
    });
    promises.push(promise);
  }

  Promise.all(promises)
    .then(() => {
      zip.generateAsync({ type: "blob" }).then((content) => {

        // 生成Blob二进制流
        FileSaver.saveAs(content, "打包图片"); // 利用file-saver保存文件  自定义文件名
        _this.$message.success("压缩完成！");
        hideFullScreenLoading()
      });
    })
    .catch((res) => {
        hideFullScreenLoading()
      _this.$message.error('压缩失败！')
    });
}


export { downloadimgWithWords, getImgArrayBuffer, atchDownload}