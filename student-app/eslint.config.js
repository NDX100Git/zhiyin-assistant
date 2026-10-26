/**
 * 学生端 ESLint 配置（ESLint 9 扁平配置）。
 *
 * 目标：能跑通现网代码的「最小可用」检查——语法错误、未定义变量、Vue 里明确的
 * 写错（v-for 缺 key、v-if 与 v-for 同级、重复属性），不做风格争论。
 * 规则只逐条放宽，每条都写了原因；没有关掉整类规则。
 */
import vue from 'eslint-plugin-vue'
import globals from 'globals'

export default [
  {
    // 构建产物与小程序产物不入检查；node_modules 由 ESLint 默认忽略
    ignores: ['dist/**', 'unpackage/**', 'src/uni_modules/**', '.nm-build/**'],
  },
  ...vue.configs['flat/essential'],
  {
    files: ['**/*.{js,mjs,vue}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        // uni-app 运行期全局：不声明就会满屏 no-undef
        uni: 'readonly',
        wx: 'readonly',
        plus: 'readonly',
        getApp: 'readonly',
        getCurrentPages: 'readonly',
        // 条件编译宏（// #ifdef H5 等）在构建时被替换，源码里不会出现
      },
    },
    rules: {
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
      // 页面组件名由 uni-app 路由约定决定（index/order/mine 等），不是多词组件
      'vue/multi-word-component-names': 'off',
      // 本项目页面就是「一页一文件」，单文件组件里集中写模板，不强制拆分
      'vue/max-len': 'off',
    },
  },
  {
    // 测试用 node 内置 test runner，需要 node 全局
    files: ['test/**/*.{js,mjs}'],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    // 现网代码里预存的未使用变量，逐文件降级为警告，别处同类问题仍报错。
    // - src/pages/mine/detail.vue：`visible` 赋值后没用
    // - src/pages/mine/replace.vue：`multiFile` 赋值后没用
    // - src/ui/pullRefresh.js：`tracking` 赋值后没用
    // 这三个文件不在本轮改动范围（本轮只改下单页/我的页/config），
    // 删这三行属于别的任务；下一轮清掉后删掉本段，规则自然回到 error。
    files: [
      'src/pages/mine/detail.vue',
      'src/pages/mine/replace.vue',
      'src/ui/pullRefresh.js',
    ],
    rules: { 'no-unused-vars': 'warn' },
  },
]
