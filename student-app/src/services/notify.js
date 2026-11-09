/**
 * 通知与轮询适配层。
 *
 * V1：前端轮询 + Toast；V2 可替换为微信订阅消息 / APP 推送，接口保持不变。
 * 轮询必须由页面 onShow 启动、onHide 停止，避免后台空跑。
 */

export function notify(message, type = 'info') {
  if (!message) {
    return
  }
  uni.showToast({
    // 60 字上限只是防止异常长的文案把提示条撑满整屏；原来的 30 字会把正常的
    // 提示拦腰截断（例如「只支持 PDF、Word、PPT、Excel，当前选的是 .md」会丢掉最后的格式），
    // 用户看到半句话反而不知道出了什么事。提示条本身会自动换行。
    title: String(message).slice(0, 60),
    icon: type === 'success' ? 'success' : 'none',
    duration: 2200,
  })
}

export function notifyError(error) {
  // 用户在选文件面板点了取消，不算错误，不该弹提示
  if (error && error.canceled) {
    return
  }
  notify((error && error.message) || String(error || '操作失败'))
}

/**
 * 创建轮询器：start() 立即执行一次再定时执行，stop() 清理定时器。
 */
export function createPoller(task, interval) {
  let timer = null
  let running = false

  async function tick() {
    if (running) {
      return
    }
    running = true
    try {
      await task()
    } catch (error) {
      console.warn('[polling]', error)
    } finally {
      running = false
    }
  }

  return {
    start() {
      if (timer) {
        return
      }
      tick()
      timer = setInterval(tick, interval)
    },
    stop() {
      if (timer) {
        clearInterval(timer)
        timer = null
      }
    },
    refresh: tick,
  }
}
