import {request} from "@/api/request.js"
export function createSrc(formdata, options = {}) {
    return request({
        method: 'POST',
        url: '/api/file/upload',
        data:formdata,
        // 上传自带进度通知（getUploadImg）：silent 避免全屏锁死且保证取消按钮可点
        silent: true,
        // 透传上传进度回调与取消信号（8GB 大文件 UX：进度可取消，江西 F2 同款）
        signal: options.signal,
        onUploadProgress: options.onUploadProgress,
        transformRequest: [function(data, headers) {
            delete headers.post['Content-Type']
            return data
        }],
        headers:{
            'Accept': 'multipart/form-data',
            'Content-Type': 'multipart/form-data'
        }
    })
}
export function imgUpload(data,funUrl){
    return request({
        method:'POST',
        url:`/api/analysis/${funUrl}`,
        data
    })
}

export function prePhotoHandle(data){
    return request({
        method:'POST',
        url:'/api/analysis/image_pre',
        data
    })
}

export function getCustomModel(model_type){
    return request({
        method:'GET',
        url:`/api/model/list/${model_type}`
    })
}

export function kmlRoiInfer(data){
    return request({
        method:'POST',
        url:'/api/analysis/kml_roi_inference',
        data
    })
}
