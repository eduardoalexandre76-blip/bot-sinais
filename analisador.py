import os, json, requests
import pandas as pd
import yfinance as yf

PAR = "EURUSD=X"
ARQ = "sinais.json"
FMT = "%Y-%m-%d %H:%M"
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]

def enviar(txt):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT, "text": txt}, timeout=20)

df = yf.download(PAR, period="2d", interval="5m", progress=False)
if df.empty or len(df) < 40:
    raise SystemExit("sem dados")
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
df = df.iloc[:-1]
df.index = df.index.strftime(FMT)
c = df["Close"]

d = c.diff()
ganho = d.clip(lower=0).rolling(14).mean()
perda = (-d.clip(upper=0)).rolling(14).mean()
rsi = 100 - 100 / (1 + ganho / perda)
ma = c.rolling(20).mean()
sd = c.rolling(20).std()
sup = ma + 2 * sd
inf = ma - 2 * sd

try:
    sinais = json.load(open(ARQ))
except Exception:
    sinais = []

ultima = pd.Timestamp(df.index[-1])
for s in sinais:
    if s["resultado"] is not None:
        continue
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
        enviar(f"Resultado {s['dir']} das {s['hora']}: {r}")
    elif t < ultima - pd.Timedelta(minutes=30):
        s["resultado"] = "SEM_DADOS"

hora = df.index[-1]
direcao = None
if rsi.iloc[-1] < 30 and c.iloc[-1] < inf.iloc[-1]:
    direcao = "CALL"
elif rsi.iloc[-1] > 70 and c.iloc[-1] > sup.iloc[-1]:
    direcao = "PUT"

if direcao and not any(s["hora"] == hora for s in sinais):
    preco = float(c.iloc[-1])
    sinais.append({"hora": hora, "dir": direcao,
                   "entrada": preco, "resultado": None})
    enviar(f"SINAL EUR/USD 5min: {direcao}\n"
           f"Vela das {hora}\nPreco: {preco:.5f}\n"
           f"Expira em 5 min. (TESTE)")

v = [s for s in sinais if s["resultado"] in ("GANHOU", "PERDEU")]
g = sum(1 for s in v if s["resultado"] == "GANHOU")
if v:
    enviar(f"Placar: {g} ganhos / {len(v)-g} perdas "
           f"({100*g/len(v):.1f}% de acerto)")

json.dump(sinais, open(ARQ, "w"), indent=1)
