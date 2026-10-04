# Agente autónomo de trading simulado

Proyecto universitario sobre **agentes de IA**. Un agente basado en un modelo de lenguaje (LLM) analiza el mercado de BTC/USDT usando herramientas, razona, decide **BUY / SELL / HOLD** y ejecuta la operación en un entorno **100 % simulado**, con dinero virtual.

> **Aviso.** Proyecto experimental y educativo. Todo es una simulación: no se opera con dinero real, no se usan claves de ningún exchange y los resultados **no constituyen una estrategia financiera validada ni garantía de rentabilidad**. El agente no predice el mercado.

## Qué hace

En cada ciclo el agente **percibe** el mercado consultando herramientas, **analiza** indicadores y su cartera, **razona**, **decide**, **actúa** en un broker simulado y **observa** el resultado (queda en el historial y lo consulta en el ciclo siguiente).

```
Datos (CSV local / Binance público)
        |
Herramientas: get_market_data · calculate_indicators · get_portfolio ·
              calculate_risk · get_trade_history · execute_virtual_order
        |
Agente (LLM con bucle de herramientas) <-- observa resultados e historial
        |
Decisión estructurada (Pydantic)
        |
Control anti-alucinaciones  ->  Validación de riesgo (código)
        |
Broker simulado  ->  Cartera  ->  SQLite (trazabilidad)
```

**Arquitectura híbrida.** Código tradicional para datos, indicadores, cartera, riesgo y ejecución. IA solo para interpretar, decidir y explicar. Un filtro de eventos en el modo backtest decide *cuándo* consultar al modelo (no *qué* hacer), para gastar pocas llamadas.

## Requisitos

- Python **3.10 o superior** (recomendado 3.11 o 3.12).
- pip. Git es opcional.
- Una API key de un LLM con **function calling**. Con [Google AI Studio](https://aistudio.google.com/apikey) hay capa gratuita. También sirve Groq, OpenRouter u Ollama (local).

Verifica tus versiones:

```bash
python --version      # en Windows también: py --version
pip --version
git --version
```

## Instalación

**Windows (PowerShell)**

```powershell
cd trading-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

**macOS / Linux**

```bash
cd trading-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Si PowerShell bloquea el script de activación: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Configuración

Edita `.env`. Lo mínimo:

```env
LLM_PROVIDER=api
LLM_API_KEY=tu_clave
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-3.8-flash
```

El nombre del modelo cambia con el tiempo (por ejemplo, `gemini-2.5-flash` dejó de estar disponible para cuentas nuevas). Si recibís un error 404, ejecutá `python main.py models` para ver los modelos que tu clave puede usar y copiá el nombre exacto en `LLM_MODEL`. Después ejecutá:

```bash
python main.py check
```

Comprueba la configuración, los datos y hace **una llamada mínima al modelo** para verificar que responde y soporta herramientas. No necesitas (ni debes poner) claves de ningún exchange.

## Ejecución

```bash
python main.py scenarios                    # lista los escenarios de demostración
python main.py demo --scenario dip          # demo con el LLM real
python main.py demo --scenario all          # los cuatro escenarios
python main.py backtest --steps 150         # simulación vela por vela
python main.py history                      # decisiones de la última ejecución
streamlit run ui/app.py                     # interfaz web
```

Para probar todo sin gastar llamadas ni tener clave: agrega `--llm mock`. **El modo mock NO es IA**: decide con una regla fija y sirve solo para desarrollo y tests. No lo uses para demostrar razonamiento.

### Escenarios reproducibles

Situaciones de mercado fijas dentro de un dataset **sintético** (semilla 42): `dip` (caída fuerte), `rally` (subida con posición en ganancia), `sideways` (lateral sin señal) y `crash` (desplome, donde puede actuar el stop-loss). Lo que se controla es la situación de mercado; la decisión la toma el LLM y puede variar un poco entre ejecuciones.

### Datos reales (opcional)

```bash
python scripts/download_history.py --days 45 --out data/btcusdt_1h_real.csv
# en .env: DATA_PATH=data/btcusdt_1h_real.csv
python main.py backtest --steps 200
python main.py live            # 1 ciclo con velas actuales (cae a datos locales si no hay red)
```

## Tests

```bash
pytest
```

107 pruebas: indicadores, cartera, broker, riesgo (compra válida y sin saldo, venta válida y sin posición, límites, stop-loss), decisiones inválidas, control de alucinaciones, bucle del agente con un LLM guionado, historial, ejecución simulada de punta a punta, seguridad y la interfaz.

## Estructura

```
main.py                 CLI
config.py               configuración y límites de riesgo
agent/                  agent.py (bucle) · tools.py · llm.py · prompts.py · schemas.py · grounding.py
market/                 data_provider.py · indicators.py
simulation/             portfolio.py · broker.py · risk.py · scenarios.py · runner.py
storage/database.py     historial en SQLite
ui/app.py               interfaz Streamlit
data/                   dataset sintético + escenarios
scripts/                make_sample_data.py · download_history.py
tests/  docs/
```

## Seguridad

- No existe ninguna variable ni código para claves de exchange.
- El módulo de datos solo puede hacer `GET` a `/api/v3/klines` (datos públicos de velas); cualquier otra URL lanza `SecurityError`.
- El módulo de ejecución (`simulation/`) no importa nada de red; un test lo verifica.
- Las órdenes solo existen dentro del `SimulatedBroker`.

## Limitaciones

- Los precios del dataset de ejemplo son **sintéticos**; sirven para demostrar el comportamiento, no para evaluar rentabilidad.
- La simulación ejecuta al precio de cierre de la vela más una comisión fija del 0,1 %. No modela deslizamiento, liquidez ni latencia.
- Un LLM no es determinista: la misma situación puede producir decisiones distintas.
- Comparar contra "comprar y mantener" en tramos cortos no permite sacar conclusiones estadísticas.
- Un solo activo y una sola operación por ciclo.

## Estado de verificación

Probado en Python 3.11 con pydantic 2.13, pandas 3.0, openai 3.22, streamlit 1.64 y pytest 9.1. Verificado de punta a punta con el LLM guionado y el mock, y la interfaz arrancando en modo headless. **No se pudo verificar desde el entorno de desarrollo** la llamada a un LLM real ni la descarga desde Binance (la red estaba restringida): por eso existe `python main.py check`, que debes ejecutar tú primero.

Más detalle en `docs/`: guía paso a paso, documento técnico, guion de exposición, preguntas del profesor y plan de 7 días.
