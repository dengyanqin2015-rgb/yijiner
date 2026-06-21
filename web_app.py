"""一进二 · 手机网页版 —— 同WiFi下手机浏览器打开即可"""
import json
import os
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HOST = "0.0.0.0"
PORT = 8899


def get_local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip


def run_daily_scan():
    """跑每日扫描，返回HTML"""
    try:
        from signals.proven_scorer import scan as scan_stocks
        from data.fetcher import fetch_daily_zt_pool, fetch_breakout_pool
        from data.preprocessor import parse_board_height, clean_seal_time

        today = datetime.now().strftime("%Y%m%d")
        zt_pool = fetch_daily_zt_pool(today)
        breakout = fetch_breakout_pool(today)

        total_zt = len(zt_pool) if not zt_pool.empty else 0
        total_break = len(breakout) if not breakout.empty else 0
        break_rate = total_break / (total_zt + total_break) * 100 if (total_zt + total_break) > 0 else 0

        sentiment = "暖" if total_zt >= 80 else ("温" if total_zt >= 40 else "冷")

        picks = scan_stocks(today, top_n=10)

        html = f"""
        <div class='card'>
          <h2>📊 市场情绪</h2>
          <div class='stats'>
            <div class='stat'>涨停<b>{total_zt}</b>只</div>
            <div class='stat'>炸板率<b>{break_rate:.0f}%</b></div>
            <div class='stat'>情绪<b>{sentiment}</b></div>
          </div>
        </div>
        """

        if picks:
            best = picks[0]
            lots = max(1, int(1500 / (best.buy_price * 100)))
            cost = best.buy_price * lots * 100
            html += f"""
            <div class='card highlight'>
              <h2>⭐ 首选推荐</h2>
              <div class='pick-main'>{best.code} <span>{best.name}</span></div>
              <div class='detail'>评分 {best.score:.0f} | 预期 +{best.expected_return}% | {best.board_height}板</div>
              <div class='detail'>封板 {best.seal_time} | 换手 {best.turnover:.1f}%</div>
              <div class='action'>
                <div>买入 <b>{lots}手 × {best.buy_price}元 = {cost:.0f}元</b></div>
                <div>止盈 <b class='green'>{best.sell_price}元</b> | 止损 <b class='red'>8.82元</b></div>
              </div>
            </div>
            """

        if len(picks) > 1:
            html += """
            <div class='card'>
              <h2>📋 全部候选</h2>
              <table>
                <tr><th>#</th><th>代码</th><th>名称</th><th>评分</th><th>涨幅</th><th>连板</th></tr>"""
            for i, p in enumerate(picks[1:11], 2):
                html += f"<tr><td>{i}</td><td>{p.code}</td><td>{p.name}</td><td>{p.score:.0f}</td><td>+{p.expected_return}%</td><td>{p.board_height}板</td></tr>"
            html += "</table></div>"

        return html

    except Exception as e:
        return f"<div class='card'><p style='color:#ff4444'>扫描失败: {e}</p><p>请确认已收盘(15:00后)</p></div>"


PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>一进二 · 打板助手</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:#0f0f0f;color:#eee;padding:12px;max-width:500px;margin:auto}
h1{font-size:20px;text-align:center;padding:12px 0;color:#ffd700}
.card{background:#1a1a2e;border-radius:12px;padding:16px;margin-bottom:12px}
.card h2{font-size:16px;margin-bottom:8px;color:#ffd700}
.stats{display:flex;gap:12px}
.stat{background:#16213e;padding:10px 16px;border-radius:8px;text-align:center;flex:1}
.stat b{display:block;font-size:24px;color:#4fc3f7}
.highlight{border:2px solid #ffd700}
.pick-main{font-size:20px;margin:8px 0}
.pick-main span{color:#aaa;font-size:14px}
.detail{color:#aaa;font-size:13px;margin:2px 0}
.action{margin-top:12px;padding:12px;background:#16213e;border-radius:8px}
.action div{margin:4px 0;font-size:15px}
.green{color:#4caf50}.red{color:#ff5252}
table{width:100%;font-size:13px;border-collapse:collapse}
th,td{padding:8px 4px;text-align:left;border-bottom:1px solid #333}
th{color:#aaa;font-size:12px}
.chat-box{margin-top:12px}
.chat-box textarea{width:100%;background:#16213e;color:#eee;border:none;border-radius:8px;padding:12px;font-size:15px;resize:none;height:60px}
.chat-box button{width:100%;padding:12px;background:#ffd700;color:#000;border:none;border-radius:8px;font-size:16px;font-weight:bold;margin-top:8px}
.reply{background:#16213e;padding:12px;border-radius:8px;margin-top:8px;font-size:14px;white-space:pre-wrap}
.refresh{text-align:center;padding:8px;color:#888;font-size:12px;cursor:pointer}
</style>
</head>
<body>
<h1>🀄 一进二 · 打板助手</h1>
<div id="scan">
  <p style="text-align:center;color:#888;padding:40px">点击下方"刷新数据"</p>
</div>
<div class="chat-box">
  <textarea id="msg" placeholder="输入问题...如：今天买什么"></textarea>
  <button onclick="send()">发送</button>
  <div id="reply"></div>
</div>
<div class="refresh" onclick="location.reload()">🔄 刷新数据</div>
<script>
async function refresh(){
  document.getElementById('scan').innerHTML='<p style="text-align:center;color:#888;padding:40px">加载中...</p>';
  try{
    const r=await fetch('/scan');
    const html=await r.text();
    document.getElementById('scan').innerHTML=html;
  }catch(e){
    document.getElementById('scan').innerHTML='<p style="color:#ff4444">连接失败，请确认电脑端已启动</p>';
  }
}
async function send(){
  const msg=document.getElementById('msg').value;
  if(!msg)return;
  document.getElementById('reply').innerHTML='<div class="reply">思考中...</div>';
  try{
    const r=await fetch('/chat?msg='+encodeURIComponent(msg));
    const text=await r.text();
    document.getElementById('reply').innerHTML='<div class="reply">'+text+'</div>';
  }catch(e){
    document.getElementById('reply').innerHTML='<div class="reply" style="color:#ff4444">发送失败</div>';
  }
}
document.getElementById('msg').addEventListener('keydown',function(e){
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}
});
refresh();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, content, content_type="text/html; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        params = parse_qs(urlparse(self.path).query)

        if path == "/" or path == "/index.html":
            self._send(PAGE_HTML)

        elif path == "/scan":
            html = run_daily_scan()
            self._send(html)

        elif path == "/chat":
            msg = params.get("msg", [""])[0]
            reply = self._process_chat(msg)
            self._send(reply)

        else:
            self.send_response(404)
            self.end_headers()

    def _process_chat(self, msg):
        """简单关键词回复 + 可扩展"""
        msg_lower = msg.lower()

        if any(w in msg_lower for w in ["买", "推荐", "选股", "买什么"]):
            try:
                from signals.proven_scorer import scan as scan_stocks
                today = datetime.now().strftime("%Y%m%d")
                picks = scan_stocks(today, top_n=5)
                if picks:
                    best = picks[0]
                    return f"首选 {best.code} {best.name}\\n评分{best.score:.0f} 预期+{best.expected_return}%\\n现价{best.price}元\\n建议买入价{best.buy_price}元\\n止盈{best.sell_price}元"
                return "今日暂无符合条件的标的。可能原因：非交易日/数据未更新/无优质标的"
            except Exception as e:
                return f"扫描出错: {e}\\n请15:00收盘后再试"

        elif any(w in msg_lower for w in ["持仓", "股票", "002809", "600067"]):
            return "当前持仓：\\n002809 红墙股份 成本8.44 100股\\n600067 冠城大通 挂3.48待成交 600股\\n止损：红墙8.36 冠城3.20"

        elif any(w in msg_lower for w in ["卖", "止损", "止盈", "出"]):
            return "卖出规则：\\n- 次日开盘涨3%+ → 止盈卖出\\n- 次日开盘跌5%+ → 止损割肉\\n- 持仓超过2天 → 不管盈亏都卖\\n- 红墙目标10元 止损8.36\\n- 冠城目标3.98元 止损3.20"

        elif any(w in msg_lower for w in ["策略", "规则", "怎么玩"]):
            return "当前策略（基于522笔真实数据验证）：\\n1. 主板非ST\\n2. 10:00前封板\\n3. 换手率<8%\\n4. 非一字板\\n5. 次日开盘卖\\n\\n历史胜率约60%+"

        else:
            return f"收到：{msg}\\n\\n可问：今天买什么 | 怎么看持仓 | 怎么卖 | 策略规则"


if __name__ == "__main__":
    ip = get_local_ip()
    print(f"""
╔══════════════════════════════════════════╗
║  一进二 · 手机网页版已启动               ║
║                                          ║
║  📱 手机打开浏览器访问:                   ║
║  http://{ip}:{PORT}              ║
║                                          ║
║  确保手机和电脑在同一个WiFi               ║
║  按 Ctrl+C 停止服务                       ║
╚══════════════════════════════════════════╝
    """)
    server = HTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()
