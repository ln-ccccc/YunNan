import {request} from  "@/api/request.js"

export function historyGetPage(page,limit,type){
    return request({
        method:'GET',
        url:'/api/history/list',
        params:{
            page:page,
            limit:limit,
            type:type
        }
    })
}

export function historyDelete(data){
    return request({
        method:'DELETE',
        url:'api/history/batchRemove',
        data
    })
}

export function historyDeleteOne(id){
    return request({
        method:'DELETE',
        url:'api/history/removeOne',
        data:{ id }
    })
}

export function historyClearByType(type){
    return request({
        method:'DELETE',
        url:'api/history/clearByType',
        data:{ type }
    })
}

export function flashHistoryGetPage(page, limit, projectId){
    return request({
        method:'GET',
        url:'/api/analysis/kml_roi_history',
        params:{
            page,
            limit,
            ...(projectId ? { project_id: projectId } : {})
        }
    })
}

export function flashHistoryDeleteOne(record_id){
    return request({
        method:'DELETE',
        url:'/api/analysis/kml_roi_history/item',
        data:{ record_id }
    })
}

export function flashHistoryClear(){
    return request({
        method:'DELETE',
        url:'/api/analysis/kml_roi_history/clear'
    })
}
