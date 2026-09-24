import * as echarts from 'echarts';

// 江西同款横向条形图配置：统一渐变配色 + 长类目名换行不截断 + tooltip confine。
// 右栏（修复后地类/开采方式统计）与左栏（修复方式 TOP5）共用，保证柱状图风格一致。
export function makeStatBarOption({ names, values, unit = '个' }) {
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: 'rgba(9, 23, 34, 0.96)',
      borderColor: 'rgba(112, 160, 151, 0.3)',
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: '#d3e1dd', fontSize: 11 },
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
      axisLabel: { color: '#78918f', fontSize: 10, margin: 8 },
    },
    yAxis: {
      type: 'category',
      // inverse 让数量最多的一档排在顶部，末位桶（如未知）落在底部
      inverse: true,
      data: names,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: '#a2b8b3',
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
        barWidth: '60%',
        itemStyle: {
          color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [
            { offset: 0, color: '#7fd8a6' },
            { offset: 0.62, color: '#54997a' },
            { offset: 1, color: '#e2c285' },
          ]),
        },
      },
    ],
  };
}
