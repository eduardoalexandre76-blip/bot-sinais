import os, json, time, requests
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
DURACAO = 285  # segundos que cada execução fica ligada vigiando o mercado
M5 = pd.Timedelta(minutes=5)
M1 = pd.Timedelta(minutes=1)


def enviar(txt):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT, "text": txt},
            timeout=20,
        )
        print("telegram:", r.status_code, r.text[:120])
        return r.status_code == 200
    except Exception as ex:
        print("erro telegram:", type(ex).__name__)
        return False


def brt(h):
    """Texto de horario (UTC) -> HH:MM de Brasilia."""
    t = pd.Timestamp(h).tz_localize("UTC").tz_convert("America/Sao_Paulo")
    return t.strftime("%H:%M")


def nome_dir(d):
    return "🟢 COMPRA (CALL) ⬆️" if d == "CALL" else "🔴 VENDA (PUT) ⬇️"


def baixar(par, intervalo, periodo):
    try:
        df = yf.download(par, period=periodo, interval=intervalo, progress=False)
    except Exception as ex:
        print("erro download", par, type(ex).__name__)
        return None
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    idx = df.index
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    df.index = idx
    return df


def direcao_de(df):
    c = df["Close"]
    d = c.diff()
    ganho = d.clip(lower=0).rolling(14).mean()
    perda = (-d.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + ganho / perda)
    ma = c.rolling(20).mean()
    sd = c.rolling(20).std()
    r, p = float(rsi.iloc[-1]), float(c.iloc[-1])
    if r < 40 and p < float((ma - 1.5 * sd).iloc[-1]):
        return "CALL", r, p
    if r > 60 and p > float((ma + 1.5 * sd).iloc[-1]):
        return "PUT", r, p
    return None, r, p


def preco_em(df, t):
    s = df["Close"].loc[:t]
    return float(s.iloc[-1]) if len(s) else None


def placar(sinais):
    v = [s for s in sinais if s.get("enviado") and s["resultado"] in ("GANHOU", "PERDEU")]
    if not v:
        return
    g = sum(1 for s in v if s["resultado"] == "GANHOU")
    taxa = f"{100 * g / len(v):.1f}".replace(".", ",")
    enviar(f"📈 PLACAR\n{LINHA}\n✅ {g} ganhos  |  ❌ {len(v) - g} perdas\n🎯 {taxa}% de acerto")


def salvar(sinais):
    json.dump(sinais, open(ARQ, "w"), indent=1)


try:
    sinais = json.load(open(ARQ))
except Exception:
    sinais = []

# sinais do formato antigo que ficaram pendentes são encerrados em silêncio
for s in sinais:
    if s["resultado"] is None and "exp_t" not in s:
        s["resultado"] = "SEM_DADOS"

# teste manual: mostra que o bot está vivo
if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
    linhas = []
    for par, nome in PARES.items():
        df = baixar(par, "5m", "2d")
        if df is not None and len(df) > 40:
            _, r, _ = direcao_de(df)
            linhas.append(f"{nome}: RSI {r:.0f}")
    enviar("✅ Bot ligado e vigiando\n" + LINHA + "\n" + "\n".join(linhas)
           + "\n" + LINHA + "\nO sinal sai ~2 min antes da entrada, quando RSI < 40 "
           "(compra) ou > 60 (venda) e o preço passa da banda.")

inicio = time.time()
avaliados = set()

while True:
    agora = pd.Timestamp.now(tz="UTC")
    T = agora.ceil("5min")  # próximo fechamento de vela = horário de entrada
    rem = (T - agora).total_seconds()

    # 1) Sinais novos: analisa a vela ainda aberta, ~2 min antes da entrada
    if 25 <= rem <= 130 and T not in avaliados:
        avaliados.add(T)
        vela = (T - M5).strftime(FMT)
        for par, nome in PARES.items():
            df = baixar(par, "5m", "2d")
            if df is None or len(df) < 40:
                continue
            if df.index[-1] != T - M5:
                print("dados atrasados:", nome, df.index[-1])
                continue
            direcao, r, p = direcao_de(df)
            if not direcao:
                continue
            if any(s.get("par") == par and s.get("vela") == vela for s in sinais):
                continue
            ok = enviar(
                f"📊 NOVO SINAL\n{LINHA}\n"
                f"💱 Par: {nome}\n"
                f"{nome_dir(direcao)}\n"
                f"⏰ Entre às {brt(T.strftime(FMT))} em ponto (Brasília)\n"
                f"⌛ Expira às {brt((T + M5).strftime(FMT))} (5 min)\n"
                f"💰 Preço agora: {p:.5f}\n"
                f"{LINHA}\n"
                f"⚠️ Teste: use conta demo."
            )
            sinais.append({
                "par": par, "vela": vela,
                "entrada_t": T.strftime(FMT),
                "exp_t": (T + M5).strftime(FMT),
                "dir": direcao, "preco_sinal": p,
                "entrada": None, "resultado": None, "enviado": ok,
            })
        salvar(sinais)

    # 2) Resultados: assim que a operação expira
    cache = {}
    mudou = False
    for s in sinais:
        if s["resultado"] is not None:
            continue
        exp = pd.Timestamp(s["exp_t"], tz="UTC")
        if agora < exp + pd.Timedelta(seconds=5):
            continue
        ent_t = pd.Timestamp(s["entrada_t"], tz="UTC")
        par = s["par"]
        if par not in cache:
            cache[par] = baixar(par, "1m", "1d")
        df = cache[par]
        # só resolve quando a vela de 1 min do vencimento já fechou
        if df is None or df.index[-1] < exp:
            if agora > exp + pd.Timedelta(minutes=8):
                s["resultado"] = "SEM_DADOS"
                mudou = True
            continue
        e = preco_em(df, ent_t - M1)
        f = preco_em(df, exp - M1)
        if e is None or f is None:
            continue
        s["entrada"] = e
        if f == e:
            res = "EMPATE"
        elif (s["dir"] == "CALL") == (f > e):
            res = "GANHOU"
        else:
            res = "PERDEU"
        s["resultado"] = res
        mudou = True
        if s.get("enviado"):
            icone = {"GANHOU": "✅ GANHOU", "PERDEU": "❌ PERDEU", "EMPATE": "➖ EMPATE"}[res]
            enviar(
                f"{icone}\n{LINHA}\n"
                f"💱 {PARES.get(par, par)}\n"
                f"{nome_dir(s['dir'])}\n"
                f"⏰ Entrada {brt(s['entrada_t'])} → expirou {brt(s['exp_t'])}\n"
                f"💰 {e:.5f} → {f:.5f}"
            )
            placar(sinais)
    if mudou:
        salvar(sinais)

    if time.time() - inicio >= DURACAO:
        break
    time.sleep(15)

salvar(sinais)
