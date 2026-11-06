# legendas-tiktok

Gera SRT curto (legenda "de TikTok": 2–3 palavras por linha, timing por palavra,
pausas respeitadas) a partir de qualquer vídeo ou áudio. O SRT entra no FilmCraft
como caption track, ou em qualquer editor que aceite SRT/WebVTT.

## Interface gráfica (jeito mais fácil)

Duplo clique em **`Legendas.bat`**. Abre uma janela escura com:

1. **Escolher…** o vídeo ou áudio
2. Ajustar (opcional): palavras por linha, modelo, idioma, chips de estilo
3. **Gerar legendas**
4. **Abrir pasta** quando terminar

O `.srt` é salvo do lado do vídeo, com o mesmo nome. As opções ficam salvas em
`gui_config.json` e voltam na próxima abertura.

O botão **Preset TikTok** volta tudo pro padrão: 2 palavras, caixa alta, contínua.

Se preferir arrastar o arquivo: jogue o vídeo em cima do `legendar.bat` (CLI,
mesmo preset).

## Auto-editor pela interface (fase 3)

Duplo clique em **`Autoeditar.bat`** — ou no botão **Auto-editar** dentro da
janela de legendas. Passo a passo:

1. **Procurar** o vídeo
2. Ajustar: quantos clipes, duração alvo, duração mínima, palavras por linha,
   altura e reframe
3. Chips: **Gancho no topo**, **Gancho com IA**, **Caixa**.
   **Preset TikTok** volta pro padrão (3 clipes de 30s, 2 palavras, crop 1080×1920)
4. **Gerar clipes**

Enquanto roda, cada etapa aparece na barra de status; **Ver log** mostra a saída
completa e **Cancelar** mata o processo (inclusive o ffmpeg/PyAV em curso).

No fim aparecem cartões com miniatura do clipe, score, duração, gancho e dois
botões: **▶ Abrir** (player padrão do Windows) e **Pasta** (abre o Explorer
com o arquivo selecionado). Clique na miniatura também abre o vídeo.

Os cartões são lidos do manifesto `video_tiktok_clips.json`, gravado pelo
`autoedit.py` junto dos clipes:

```json
{
  "source": "C:\\...\\video.mp4",
  "clips": [
    {"index": 1, "path": "..._clip1.mp4", "start": 2.5, "end": 8.7,
     "dur": 6.24, "score": 0.646, "hook": "JOGO LANÇAR VÃO ADICIONAR UM MAPA",
     "text": "DE NOVEMBRO TÁ QUANDO O JOGO LANÇAR..."}
  ]
}
```

O manifesto serve pra automatizar depois (montar uma timeline, subir por API,
gerar thumbnail em lote). As opções da janela ficam em `autoedit_config.json`.

## Instalação

```powershell
pip install faster-whisper "av==18.1.0" pillow
```

Não precisa ffmpeg no PATH — a decodificação usa PyAV, que vem na instalação.
Na primeira execução o modelo do Whisper é baixado (~500 MB no `small`).

> **Não atualize o `av` para 19.x.** O faster-whisper 1.2.1 quebra com
> `TypeError: open() got an unexpected keyword argument 'metadata_errors'`.

Teste rápido sem baixar modelo:

```powershell
python make_srt.py --self-test
```

## Auto-editor (fase 1)

`autoedit.py` faz o corte automático: transcreve, **remove silêncios**, reframa
16:9 → **9:16**, queima **legenda animada** (palavra ativa em amarelo e maior) e
exporta MP4 pronto pra postar.

```powershell
python autoedit.py video.mp4
python autoedit.py video.mp4 --reframe blur      # fundo desfocado, frame inteiro
python autoedit.py video.mp4 -w 1                # uma palavra por vez
python autoedit.py video.mp4 --max-silence 0.25  # corta mais agressivo
python autoedit.py video.mp4 --box               # caixa preta atrás do texto
python autoedit.py video.mp4 --keep-silence      # só legenda e reframe, sem cortar
```

Saída: `video_tiktok.mp4` (1080×1920, 30fps, H.264 + AAC 192k).

| Flag | Padrão | O que faz |
| --- | --- | --- |
| `--max-silence` | `0.35` | pausa maior que isso vira corte |
| `--lead` / `--tail` | `0.12` / `0.18` | respiro antes/depois da fala |
| `--reframe` | `crop` | `crop`, `blur` ou `fit` |
| `--crop-x` | `0.5` | desloca o corte horizontal (0 = esquerda) |
| `--height` | `1920` | altura final; largura = 9/16 dela |
| `--fps` | `30` | 60fps na fonte vira 30 na saída (sem duplicar frame) |
| `--crf` | `20` | qualidade/x tamanho do H.264 |

Não corta respiro no meio da palavra, não deixa legenda fora de sincronia: o
corte e a legenda usam o mesmo mapa de tempo da fonte.

Custo: ~1,2x a duração do vídeo na CPU (20s de vídeo ≈ 25s de render).

## Fase 2: escolha automática dos melhores trechos

```powershell
python autoedit.py video.mp4 --highlights
python autoedit.py video.mp4 --highlights --clips 5 --clip-len 30
python autoedit.py video.mp4 --highlights --clip-len 10 --clips 3 --min-len 6   # vídeo curto
python autoedit.py video.mp4 --highlights --no-hook                              # sem gancho
python autoedit.py video.mp4 --highlights --llm-hook                             # gancho via LLM
```

Gera `video_tiktok_clip1.mp4`, `clip2.mp4`, ... e o manifesto
`video_tiktok_clips.json` (usado pelos cartões da interface).

Como escolhe: mede, na timeline já cortada,

| Sinal | Peso | O que capta |
| --- | --- | --- |
| densidade de fala | 0.50 | trecho sem silêncio e cheio de conteúdo |
| energia RMS | 0.32 | empolgação, pico de voz |
| troca de cena | 0.18 | movimento, cortes de imagem |

Janelas de `--clip-len` segundos, passo de 3s, top N sem sobreposição. As bordas
são encaixadas em início/fim de palavra, então o corte nunca cai no meio da frase.

**Gancho**: texto grande no topo, em 2–3 linhas, com caixa própria e a legenda
rodando embaixo por **10s** (ajuste com `--hook-dur`). Sem LLM usa heurística
(palavra-chave, número, palavra de impacto). Com LLM:

```powershell
$env:OPENAI_API_KEY="sk-..."
python autoedit.py video.mp4 --highlights --llm-hook
```

Aceita também `OPENROUTER_API_KEY` + `OPENAI_BASE_URL`, e `AUTOEDIT_HOOK_MODEL`
(padrão `gpt-4o-mini`). Se a chamada falhar, cai na heurística sem quebrar.

Limite honesto: score não sabe o que é engraçado. Ele acha "trecho denso e
energético". Revisão humana ainda é necessária.

## Uso (linha de comando)

```powershell
# padrão: 2 palavras por cue, PT, minúsculas, SRT ao lado do vídeo
python make_srt.py video.mp4

# estilo TikTok: 2 palavras, CAIXA ALTA, cues contínuas
python make_srt.py video.mp4 -w 2 --upper --tight

# uma palavra por vez (o "palavra a palavra" clássico)
python make_srt.py video.mp4 -w 1 --upper --tight

# 3 palavras, arquivo de saída explícito, também gera .vtt
python make_srt.py video.mp4 -w 3 -o legenda.srt --vtt

# áudio em inglês, modelo melhor
python make_srt.py audio.wav -l en -m medium
```

Arrastar o vídeo em cima do `legendar.bat` roda o preset TikTok.

## Opções úteis

| Flag | Padrão | Para quê |
| --- | --- | --- |
| `-w, --words` | `2` | máximo de palavras por cue (`1` = palavra por vez) |
| `--max-chars` | `24` | limite de caracteres por cue (`0` desliga) |
| `--max-dur` | `2.4` | duração máxima da cue em segundos |
| `--gap` | `0.45` | pausa que força quebra de cue |
| `--pad` | `0.06` | respiro antes/depois do texto |
| `--min-dur` | `0.35` | duração mínima (nada de flash ilegível) |
| `--tight` | off | fecha buracos curtos, visual contínuo |
| `--tight-max` | `0.6` | só fecha buracos até N s; pausa maior fica vazia |
| `--upper` | off | TEXTO EM CAIXA ALTA |
| `--keep-punct` | off | mantém pontuação (padrão remove) |
| `--no-merge` | off | não junta fragmentos hifenizados (`guarda` + `-roupa`) |
| `-m, --model` | `small` | `tiny`/`base`/`small`/`medium`/`large-v3` |
| `-l, --lang` | `pt` | `auto` detecta |

Modelo x qualidade em português: `small` já dá conta de fala limpa; `medium` se
tiver gíria, sotaque forte ou áudio com música. `large-v3` é melhor e bem mais
lento.

## Problemas conhecidos

- **`TypeError: open() got an unexpected keyword argument 'metadata_errors'`**
  O `av` 19 quebrou a API que o faster-whisper 1.2.1 usa. Correção:
  ```powershell
  pip install "av==18.1.0"
  ```
- **`Library cublas64_12.dll is not found`**
  GPU sem CUDA runtime completo. O script detecta e cai para CPU sozinho, sem
  precisar de nada. Para forçar: `--device cpu`.
- **Primeira execução demora**
  Baixa o modelo do Hugging Face (~500 MB no `small`). Depois fica em cache.

## Pipeline com FilmCraft

1. Corta o vídeo no FilmCraft primeiro (`File › New › Sequence From Clip`).
2. Exporta o corte (ou usa o áudio original).
3. Roda o script no arquivo exportado:
   ```powershell
   python make_srt.py corte.mp4 -w 2 --upper --tight
   ```
4. No FilmCraft: **importa o vídeo, cria a sequence primeiro, e só depois
   arrasta o `.srt`**. A ordem importa — SRT importado sem sequence aberta cria
   uma sequence sem vídeo.
5. O `.srt` vira caption track **C1** acima dos vídeos.
6. Estilo: `Window › Text` → aba **Captions** → faixa de estilo do track:
   - tamanho **70–90** (px em frame 1080, escala sozinho)
   - cor branca + **caixa de fundo** (essencial em cena clara)
   - alinhamento **centro**, posição **bottom**
   - **margem 0.15–0.2** para não ficar embaixo da UI do TikTok
7. `File › Export › Media…` → seção **Captions** → **Burn Captions Into Video**.

Burn-in re-encoda o vídeo inteiro. Faz sempre por último.

## Limites do FilmCraft (importante)

- Fonte do burn-in é **fixa em Inter SemiBold**. `<i>`/`<b>` no SRT é ignorado.
- Sem animação, sem highlight por palavra, sem template.
- O estilo é **por track**, não por linha.
- Se quiser o visual animado de verdade (palavra pulando, caixa colorida),
  exporta do FilmCraft e faz a legenda no CapCut/Submagic. O SRT gerado aqui
  serve para importar lá também.

## Dicas de legenda

- Word-by-word só funciona com `--tight`; sem ele aparecem buracos.
- `--upper` combina com `-w 2`; `-w 3` em caixa alta costuma estourar a largura.
- Se a legenda atrasar/adiantar em relação ao vídeo todo, o SRT é de outra versão
  do corte — regera a partir do arquivo certo.
