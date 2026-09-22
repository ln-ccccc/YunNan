// tif/tiff 文件选择纯函数：Segmentation 与 SpectralIndices 原先各持一份逐字复制
// （isValidTiff 还出现了大小写处理漂移），统一收敛到这里供两视图及后续共享上传卡组件使用。
// readDroppedItems/walkFileTree 依赖浏览器 DataTransfer API，node --test 不执行它们，
// 模块本身无顶层浏览器调用，可被 Node 直接 import 做纯函数测试。
export function isValidTiff(fileLike) {
  const raw = fileLike?.raw || fileLike?.file || fileLike;
  const name = String(fileLike?.relativePath || raw?.webkitRelativePath || raw?.name || '');
  const suffix = name.substring(name.lastIndexOf('.') + 1).toLowerCase();
  return ['tif', 'tiff'].includes(suffix);
}

export function normalizeSelectedItems(inputFiles) {
  return inputFiles.map((item) => {
    if (item?.raw) return item;
    if (item?.file) {
      return {
        raw: item.file,
        relativePath: item.relativePath || item.file.webkitRelativePath || item.file.name,
      };
    }
    return {
      raw: item,
      relativePath: item?.webkitRelativePath || item?.name,
    };
  });
}

export function createUploadItems(files) {
  return files.map((item, index) => {
    const raw = item.raw;
    return {
      name: item.relativePath || raw.webkitRelativePath || raw.name,
      size: raw.size,
      status: 'ready',
      uid: `${raw.name}-${raw.lastModified}-${index}`,
      raw,
    };
  });
}

export function filesFromInputEvent(event) {
  return Array.from(event?.target?.files || []);
}

export async function readDroppedItems(items) {
  if (!items.length) return [];
  const entries = items
    .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
    .filter(Boolean);
  if (!entries.length) return [];
  const files = [];
  for (const entry of entries) {
    const entryFiles = await walkFileTree(entry);
    files.push(...entryFiles);
  }
  return files;
}

export function walkFileTree(entry, parentPath = '') {
  if (!entry) return Promise.resolve([]);
  if (entry.isFile) {
    return new Promise((resolve) => {
      entry.file((file) => {
        resolve([{
          file,
          relativePath: parentPath ? `${parentPath}/${file.name}` : file.name,
        }]);
      }, () => resolve([]));
    });
  }
  if (!entry.isDirectory) return Promise.resolve([]);

  const directoryPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
  const reader = entry.createReader();
  return new Promise((resolve) => {
    const allEntries = [];
    const readBatch = () => {
      reader.readEntries(async (batch) => {
        if (!batch.length) {
          let files = [];
          for (const child of allEntries) {
            const childFiles = await walkFileTree(child, directoryPath);
            files = files.concat(childFiles);
          }
          resolve(files);
          return;
        }
        allEntries.push(...batch);
        readBatch();
      }, () => resolve([]));
    };
    readBatch();
  });
}
