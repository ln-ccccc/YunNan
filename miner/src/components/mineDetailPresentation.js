export function buildClassificationItems(changeMatrixData) {
  const images = changeMatrixData?.images || {};
  const items = [];
  if (images.old) {
    items.push({
      key: 'old',
      title: '前期地物分类',
      year: changeMatrixData?.old_year ?? null,
      url: images.old,
    });
  }
  if (images.new) {
    items.push({
      key: 'new',
      title: '当前地物分类',
      year: changeMatrixData?.new_year ?? null,
      url: images.new,
    });
  }
  return items;
}

export function buildMatrixYearLabels(changeMatrixData) {
  return {
    old: changeMatrixData?.old_year ?? '前期',
    new: changeMatrixData?.new_year ?? '当前',
  };
}
