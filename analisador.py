import os, json, requests
import pandas as pd
import yfinance as yf

PARES = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD",
    "USDCAD=X": "USD/CAD",
}
ARQ = "sinais.json"
FMT = "%Y-%m-%d %H:%M"
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
LINHA = "━━━━━━━━━━━━━━━"


def enviar(txt):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT, "text": txt},
            timeout=20,
        )
        print("telegram:", r.status_code, r.text[:200])
        return r.status_code == 200
    except Exception as ex:
        print("erro telegram:", type(ex).__name__)
        return False


def brt(h, mais=0):
    """Converte o horario (UTC) para Brasilia."""
    t = pd.Timestamp(h) + pd.Timedelta(minutes=mais)
    t = t.tz_localize("UTC").tz_convert("America/Sao_Paulo")
    return t.strftime("%H:%M")


def nome_dir(d):
    return "🟢 COMPRA (CALL) ⬆️" if d == "CALL" else "🔴 VENDA (PUT) ⬇️"


def baixar(par):
    df = yf.download(par, period="2d", interval="5m", progress=False)
    if df.empty or len(df) < 40:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.iloc[:-1]  # remove a vela ainda aberta
    idx = df.index
    if idx.tz is not None:
        idx = idx.tz_convert("UTC")
    df.index = idx.strftime(FMT)
    return df


dados = {}
for par in PARES:
    try:
        df = baixar(par)
    except Exception as ex:
        print("erro em", par, ex)
        df = None
    if df is not None:
        dados[par] = df

if not dados:
    raise SystemExit("sem dados")

try:
    sinais = json.load(open(ARQ))
except Exception:
    sinais = []

# 1) Confere o resultado dos sinais antigos
for s in sinais:
    if s["resultado"] is not None:
        continue
    par = s.get("par", "EURUSD=X")
    df = dados.get(par)
    if df is None:
        continue
    c = df["Close"]
    ultima = pd.Timestamp(df.index[-1])
    t = pd.Timestamp(s["hora"])
    exp = (t + pd.Timedelta(minutes=5)).strftime(FMT)
    if exp in df.index:
        f = float(c.loc[exp])
        e = s["entrada"]
        if f == e:
            r = "EMPATE"
        elif (s["dir"] == "CALL") == (f > e):
            r = "GANHOU"
        else:
            r = "PERDEU"
        s["resultado"] = r
        if s.get("enviado"):
            icone = {"GANHOU": "✅ GANHOU", "PERDEU": "❌ PERDEU", "EMPATE": "➖ EMPATE"}[r]
            enviar(
                f"{icone}\n{LINHA}\n"
                f"💱 {PARES.get(par, par)}\n"
                f"{nome_dir(s['dir'])}\n"
                f"⏰ Entrada {brt(s['hora'], 5)} → expirou {brt(s['hora'], 10)}\n"
                f"💰 {e:.5f} → {f:.5f}"
            )
    elif t < ultima - pd.Timedelta(minutes=30):
        s["resultado"] = "SEM_DADOS"

# 2) Procura novos sinais em cada par
status = []
for par, nome in PARES.items():
    df = dados.get(par)
    if df is None:
        continue
    c = df["Close"]
    d = c.diff()
    ganho = d.clip(lower=0).rolling(14).mean()
    perda = (-d.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + ganho / perda)
    ma = c.rolling(20).mean()
    sd = c.rolling(20).std()
    sup = ma + 1.5 * sd
    inf = ma - 1.5 * sd

    status.append(f"{nome}: RSI {rsi.iloc[-1]:.0f}")
    hora = df.index[-1]
    direcao = None
    if rsi.iloc[-1] < 40 and c.iloc[-1] < inf.iloc[-1]:
        direcao = "CALL"
    elif rsi.iloc[-1] > 60 and c.iloc[-1] > sup.iloc[-1]:
        direcao = "PUT"

    ja_enviado = any(
        s["hora"] == hora and s.get("par", "EURUSD=X") == par for s in sinais
    )
    if direcao and not ja_enviado:
        preco = float(c.iloc[-1])
        ok = enviar(
            f"📊 NOVO SINAL\n{LINHA}\n"
            f"💱 Par: {nome}\n"
            f"{nome_dir(direcao)}\n"
            f"⏰ Entrada: {brt(hora, 5)} (Brasília)\n"
            f"⌛ Expira: {brt(hora, 10)} (5 minutos)\n"
            f"💰 Preço: {preco:.5f}\n"
            f"{LINHA}\n"
            f"⚠️ Teste: use conta demo."
        )
        sinais.append({"hora": hora, "par": par, "dir": direcao,
                       "entrada": preco, "resultado": None, "enviado": ok})

print(status)
if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
    enviar("✅ Bot ligado e analisando\n" + LINHA + "\n"
           + "\n".join(status)
           + "\n" + LINHA + "\nSinal sai quando RSI < 40 (compra) ou > 60 (venda) "
           "e o preço passa da banda.")

# 3) Placar (só conta sinais que chegaram no Telegram)
v = [s for s in sinais if s.get("enviado") and s["resultado"] in ("GANHOU", "PERDEU")]
g = sum(1 for s in v if s["resultado"] == "GANHOU")
if v:
    taxa = f"{100 * g / len(v):.1f}".replace(".", ",")
    enviar(f"📈 PLACAR\n{LINHA}\n✅ {g} ganhos  |  ❌ {len(v) - g} perdas\n🎯 {taxa}% de acerto")

json.dump(sinais, open(ARQ, "w"), indent=1)
