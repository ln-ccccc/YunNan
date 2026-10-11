<template>
  <footer class="page-footer">
    <a
      href="https://github.com/PaddleCV-SIG/cugrs"
      target="_blank"
      rel="noopener noreferrer"
    >查看项目说明</a>
  </footer>
</template>

<script>

export default {
  name: "Bottominfor"
};
</script>

<style scoped>
.page-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 24px;
  padding: 12px 0;
  /* 避开固定的返回顶部按钮，并保留链接焦点轮廓的空间。 */
  padding-inline-end: 64px;
  font-size: 13px;
  line-height: 1.6;
}
.page-footer a {
  color: var(--primary-color);
  padding: 4px 8px;
  border-radius: 6px;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.page-footer a:hover {
  color: var(--primary-dark);
}
</style>
