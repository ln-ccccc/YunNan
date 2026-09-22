// 分类成果类别配色（编辑器矢量渲染与图例共用）。
// class_code 0-5 固定映射；成果定义自带 rgb 数组时优先；未知类别回退品牌青。
export const CLASS_COLORS = {
  0: '#00ff00',
  1: '#008000',
  2: '#ff0000',
  3: '#ffff00',
  4: '#ff00ff',
  5: '#00bfff',
};

export const CLASS_COLOR_FALLBACK = '#2bb6ad';

// maplibre / MapboxDraw paint 用 match 表达式（按要素 user_class_code 着色）
export function classColorExpression() {
  return [
    'match',
    ['get', 'user_class_code'],
    0, CLASS_COLORS[0],
    1, CLASS_COLORS[1],
    2, CLASS_COLORS[2],
    3, CLASS_COLORS[3],
    4, CLASS_COLORS[4],
    5, CLASS_COLORS[5],
    CLASS_COLOR_FALLBACK,
  ];
}

// 图例/列表着色：定义带 rgb 数组（如 [0,128,0]）优先，其次 class_code 映射，兜底回退色
export function classColor(definition) {
  const rgb = definition?.rgb;
  if (Array.isArray(rgb) && rgb.length === 3) return `rgb(${rgb.join(',')})`;
  return CLASS_COLORS[definition?.class_code] || CLASS_COLOR_FALLBACK;
}
