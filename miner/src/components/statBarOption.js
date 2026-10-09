import * as echarts from 'echarts';

// 横向条形图：长类目名换行不截断，tooltip 限制在面板内。
// 右栏（修复后地类/开采方式统计）与左栏（修复方式 TOP5）共用，保证柱状图风格一致。
export function makeStatBarOption({ names, values, unit = '个' }) {
  return {
    backgroundColor: 'transparent',
    animation: !globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    animationDuration: 240,
    animationDurationUpdate: 180,
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: 'rgba(252, 253, 253, 0.98)',
      borderColor: '#dbe5e2',
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: '#16383c', fontSize: 11 },
      axisPointer: {
        type: 'shadow',
        shadowStyle: { color: 'rgba(91, 151, 142, 0.08)' },
      },
      formatter: (params) => {
        const item = params?.[0];
        if (!item) return '';
        return `${item.name}<br/>${item.value} ${unit}`;
      },
    },
    grid: { left: '4%', right: '8%', bottom: '6%', top: '5%', containLabel: true },
    xAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: {
        show: true,
        lineStyle: { color: 'rgba(136, 169, 162, 0.12)', type: 'dashed' },
      },
      axisLabel: { color: '#61767b', fontSize: 10, margin: 8, hideOverlap: true },
    },
    yAxis: {
      type: 'category',
      // inverse 让数量最多的一档排在顶部，末位桶（如未知）落在底部
      inverse: true,
      data: names,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: '#4b6569',
        fontSize: 11,
        margin: 10,
        width: 96,
        overflow: 'break',
        lineHeight: 14,
      },
    },
    series: [
      {
        type: 'bar',
        data: values,
        // 固定像素宽：各面板行数/图幅不同，百分比宽度会让左右柱子粗细漂移；
        // 固定 16px 保证所有柱状图粗细一致，图幅增高时长出的空间由行间留白吸收
        barWidth: 16,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [
            { offset: 0, color: '#408471' },
            { offset: 1, color: '#80ad9f' },
          ]),
        },
      },
    ],
  };
}
