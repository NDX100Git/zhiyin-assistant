import { createSSRApp } from "vue";
import App from "./App.vue";
// 测试用轨迹日志（页面、点击、坐标）。开关由 VITE_TRACE 控制，没开时这里什么都不做。
import { installTrace } from "./utils/trace.js";

export function createApp() {
	const app = createSSRApp(App);
	installTrace(app);
	return {
		app,
	};
}
