"""Imagens do MeMi BOT: banner da ajuda, level up, resumo, cartão de perfil e wrapped.

Identidade "resenha de madrugada": fundo escuro liso, tipografia Manrope, um acento de cor e o
avatar como rótulo de um disco de vinil. Tudo é desenhado em 2x e reduzido no fim.

Pillow é opcional: importar este módulo nunca falha; sem Pillow, `disponivel()` devolve False e
as funções `gerar_*` levantam RuntimeError. Fontes: Manrope (assets/fonts) e, para o que ela não
cobre, DejaVu Sans; sem nenhuma das duas, cai na fonte embutida do Pillow com ASCII simples.
Nenhuma delas desenha emojis, então o texto passa por `limpo()`.
"""

import io
import math
import unicodedata
from functools import lru_cache
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
except ImportError:  # Pillow é opcional
    Image = ImageChops = ImageDraw = ImageFilter = ImageFont = None

SS = 2
LARGURA = 1200
PASTA_FONTES = Path(__file__).with_name("assets") / "fonts"
FONTE_MANROPE = PASTA_FONTES / "Manrope.ttf"
FONTE_DEJAVU = PASTA_FONTES / "DejaVuSans.ttf"
PASTA_BANNERS = Path(__file__).with_name("assets") / "banners"
FUNDO_CARTAO = PASTA_BANNERS / "cartao.jpg"  # fundo fixo do mm!cartao (1200x400)
BANNER_AJUDA = PASTA_BANNERS / "ajuda.jpg"  # banner do mm!help (1200x400)
# Lentes dos óculos no fundo do cartão: (centro x, centro y, raio). O avatar entra na direita.
LENTE_DIREITA = (238.5, 199.5, 88.5)
LENTE_ESQUERDA = (1.5, 204.5, 88.5)
LOGO_CARTAO = (930, 0, 1200, 64)  # área do logo no fundo: fica fora do véu escuro
# O Discord mostra a imagem de 1200 px com ~440 px: nenhum texto abaixo deste corpo.
TEXTO_MINIMO = 24
PESOS = {"fino": 300, "medio": 500, "semi": 600, "negrito": 700, "forte": 800}

TINTA = (16, 17, 24)
GRAFITE = (26, 27, 39)
BRANCO = (243, 244, 248)
SLATE = (150, 154, 174)
CORAL = (240, 112, 78)
LILAS = (140, 130, 205)
DISCO = (12, 12, 18)


def disponivel():
    return Image is not None


def _exigir():
    if Image is None:
        raise RuntimeError("Pillow não está instalado (python -m pip install -r requirements.txt)")


def _rgb(cor):
    cor = 0xF0704E if cor is None else int(cor)
    return ((cor >> 16) & 255, (cor >> 8) & 255, cor & 255)


def _s(v):
    return int(round(v * SS))


def _rgba(cor, alfa=255):
    return (*cor[:3], alfa)


# ------------------------------------------------------------------ fontes e texto
@lru_cache(maxsize=None)
def _ttf_ok(caminho):
    """True se o arquivo existe e o FreeType consegue abri-lo (vazio ou corrompido = False)."""
    try:
        ImageFont.truetype(caminho, 10)
        return True
    except OSError:
        return False


def _abrir(caminho, tamanho, peso=None):
    fonte = ImageFont.truetype(str(caminho), max(1, int(tamanho)))
    if peso is not None:
        try:
            fonte.set_variation_by_axes([peso])
        except (OSError, AttributeError, ValueError, NotImplementedError):  # fonte estática
            pass
    return fonte


def _tracado(fonte, caractere):
    imagem = Image.new("L", (80, 80), 0)
    ImageDraw.Draw(imagem).text((10, 10), caractere, font=fonte, fill=255)
    return imagem.tobytes()


@lru_cache(maxsize=8192)
def _suporta(caminho, caractere):
    """True se a fonte desenha o caractere (e não o quadrado de 'sem glifo')."""
    fonte = _abrir(caminho, 30)
    return _tracado(fonte, caractere) != _tracado(fonte, "￿")


def _familias():
    return [str(c) for c in (FONTE_MANROPE, FONTE_DEJAVU) if _ttf_ok(str(c))]


def limpo(texto):
    """Só o que alguma fonte desenha; sem fontes, ASCII simples. Tira controles e emojis."""
    texto = unicodedata.normalize("NFC", str(texto or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) not in ("Cc", "Cf"))
    # Sem shaping (raqm), alfabetos da direita para a esquerda (árabe, hebraico) sairiam
    # desconexos e invertidos: é melhor descartá-los.
    texto = "".join(c for c in texto if unicodedata.bidirectional(c) not in ("R", "AL", "AN"))
    familias = _familias()
    if familias:
        texto = "".join(c for c in texto if c.isspace() or any(_suporta(f, c) for f in familias))
    else:
        texto = "".join(c for c in unicodedata.normalize("NFKD", texto) if ord(c) < 128)
    return " ".join(texto.split())


def fonte(peso, tamanho, texto=""):
    """Manrope se ela cobre o texto; senão DejaVu; senão a embutida do Pillow. `tamanho` em px lógicos."""
    px = tamanho * SS
    for caminho in _familias():
        if all(_suporta(caminho, c) for c in texto if not c.isspace()):
            return _abrir(caminho, px, PESOS[peso] if caminho == str(FONTE_MANROPE) else None)
    try:
        return ImageFont.load_default(size=int(px))
    except TypeError:  # pragma: no cover - Pillow antigo
        return ImageFont.load_default()


def largura(txt, peso, tam, tracking=0):
    txt = limpo(txt)
    medida = ImageDraw.Draw(Image.new("L", (1, 1))).textlength(txt, font=fonte(peso, tam, txt))
    return medida / SS + tracking * max(0, len(txt) - 1)


def _cortar_largura(txt, peso, tam, maximo, tracking=0):
    """Corta o texto com … até caber em `maximo` px (estimativa proporcional: poucas medições)."""
    fnt = fonte(peso, tam, txt)
    medidor = ImageDraw.Draw(Image.new("L", (1, 1)))

    def medir(t):
        return medidor.textlength(t, font=fnt) / SS + tracking * max(0, len(t) - 1)

    if medir(txt) <= maximo:
        return txt
    while len(txt) > 1:
        if medir(txt + "…") <= maximo:
            return txt + "…"
        txt = txt[: max(1, min(len(txt) - 1, int(len(txt) * maximo / medir(txt + "…"))))].rstrip()
    return txt


def ajustar(txt, peso, tam, maximo, minimo=TEXTO_MINIMO):
    """Reduz o corpo (e, por último, corta com …) até caber em `maximo` px. Devolve (texto, corpo)."""
    txt = limpo(txt)
    corpo = tam
    while corpo > minimo and largura(txt, peso, corpo) > maximo:
        corpo -= 1
    return _cortar_largura(txt, peso, corpo, maximo), corpo


def _camada(base):
    return Image.new("RGBA", base.size, (0, 0, 0, 0))


def texto(base, xy, txt, peso, tam, cor=BRANCO, ancora="ls"):
    txt = limpo(txt)
    camada = _camada(base)
    ImageDraw.Draw(camada).text(
        (_s(xy[0]), _s(xy[1])), txt, font=fonte(peso, tam, txt), fill=_rgba(cor), anchor=ancora
    )
    return Image.alpha_composite(base, camada)


def texto_espacado(base, xy, txt, peso, tam, cor=SLATE, tracking=2.4, ancora="l"):
    """Caixa-alta com espaço entre letras (rótulos). ancora: l (esquerda), m (centro), r (direita)."""
    txt = limpo(txt).upper()
    fnt = fonte(peso, tam, txt)
    desenho = ImageDraw.Draw(_camada(base))
    total = largura(txt, peso, tam, tracking)
    x = xy[0] - (total / 2 if ancora == "m" else total if ancora == "r" else 0)
    camada = _camada(base)
    d = ImageDraw.Draw(camada)
    for ch in txt:
        d.text((_s(x), _s(xy[1])), ch, font=fnt, fill=_rgba(cor), anchor="ls")
        x += desenho.textlength(ch, font=fnt) / SS + tracking
    return Image.alpha_composite(base, camada)


# ------------------------------------------------------------------ desenho
def _fundo(w, h, acento):
    W, H = _s(w), _s(h)
    grad = Image.linear_gradient("L").resize((W, H))
    base = Image.composite(
        Image.new("RGBA", (W, H), _rgba(GRAFITE)), Image.new("RGBA", (W, H), _rgba(TINTA)), grad
    )
    brilho = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    b = ImageDraw.Draw(brilho)
    b.ellipse((W * 0.62, -H * 1.0, W * 1.3, H * 0.5), fill=_rgba(LILAS, 46))
    b.ellipse((-W * 0.3, H * 0.55, W * 0.3, H * 1.6), fill=_rgba(acento, 34))
    base = Image.alpha_composite(
        base, brilho.filter(ImageFilter.GaussianBlur(_s(min(w, h) * 0.24)))
    )
    grao = Image.effect_noise((W, H), 22).convert("RGBA")
    grao.putalpha(7)
    return Image.alpha_composite(base, grao)


def _sombra_elipse(base, cx, cy, raio):
    camada = _camada(base)
    ImageDraw.Draw(camada).ellipse(
        (_s(cx - raio), _s(cy - raio * 0.94), _s(cx + raio), _s(cy + raio * 1.06)),
        fill=(0, 0, 0, 150),
    )
    return Image.alpha_composite(base, camada.filter(ImageFilter.GaussianBlur(_s(raio * 0.08))))


def _sombra_caixa(base, caixa, raio):
    camada = _camada(base)
    x0, y0, x1, y1 = [_s(v) for v in caixa]
    ImageDraw.Draw(camada).rounded_rectangle(
        (x0, y0 + _s(10), x1, y1 + _s(10)), radius=_s(raio), fill=(0, 0, 0, 140)
    )
    return Image.alpha_composite(base, camada.filter(ImageFilter.GaussianBlur(_s(14))))


def _avatar_redondo(dados, lado, cor):
    """Avatar como círculo RGBA de `lado` px (escala SS); se faltar ou for inválido, cor lisa."""
    try:
        img = Image.open(io.BytesIO(dados)).convert("RGBA").resize((lado, lado), Image.LANCZOS)
    except Exception:  # noqa: BLE001 - bytes ausentes ou inválidos
        img = Image.new("RGBA", (lado, lado), _rgba(cor))
    mascara = Image.new("L", (lado * 2, lado * 2), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, lado * 2 - 1, lado * 2 - 1), fill=255)
    img.putalpha(mascara.resize((lado, lado), Image.LANCZOS))
    return img


def _disco(base, cx, cy, raio, avatar, acento, giro=30):
    """Vinil discreto: poucos sulcos finos, reflexo suave e o avatar como rótulo."""
    W, H = base.size
    base = _sombra_elipse(base, cx, cy, raio)
    C, R = (_s(cx), _s(cy)), _s(raio)
    camada = _camada(base)
    d = ImageDraw.Draw(camada)
    d.ellipse((C[0] - R, C[1] - R, C[0] + R, C[1] + R), fill=_rgba(DISCO))
    r = 0.96
    while r > 0.58:
        rr = int(R * r)
        d.ellipse(
            (C[0] - rr, C[1] - rr, C[0] + rr, C[1] + rr),
            outline=_rgba((30, 31, 44)),
            width=max(1, _s(1)),
        )
        r -= 0.045
    d.ellipse(
        (C[0] - R, C[1] - R, C[0] + R, C[1] + R), outline=_rgba(BRANCO, 26), width=max(1, _s(1.2))
    )
    base = Image.alpha_composite(base, camada)
    reflexo = _camada(base)
    rd = ImageDraw.Draw(reflexo)
    for ang in (giro, giro + 180):
        a0, a1 = math.radians(ang - 7), math.radians(ang + 7)
        rd.polygon(
            [
                C,
                (C[0] + R * math.cos(a0), C[1] - R * math.sin(a0)),
                (C[0] + R * math.cos(a1), C[1] - R * math.sin(a1)),
            ],
            fill=_rgba(BRANCO, 16),
        )
    mascara = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mascara).ellipse((C[0] - R, C[1] - R, C[0] + R, C[1] + R), fill=255)
    reflexo.putalpha(ImageChops.multiply(reflexo.getchannel("A"), mascara))
    base = Image.alpha_composite(base, reflexo.filter(ImageFilter.GaussianBlur(_s(3))))
    rot = int(R * 0.50)
    camada = _camada(base)
    ImageDraw.Draw(camada).ellipse(
        (C[0] - rot - _s(3), C[1] - rot - _s(3), C[0] + rot + _s(3), C[1] + rot + _s(3)),
        fill=_rgba(acento),
    )
    base = Image.alpha_composite(base, camada)
    base.alpha_composite(_avatar_redondo(avatar, rot * 2, acento), (C[0] - rot, C[1] - rot))
    return base


def _rotulo(base, xy, txt, acento, tam=26):
    """Marcador de seção: filete de acento + texto espaçado."""
    camada = _camada(base)
    ImageDraw.Draw(camada).rounded_rectangle(
        (_s(xy[0]), _s(xy[1] - tam * 0.62), _s(xy[0] + 30), _s(xy[1] - tam * 0.44)),
        radius=_s(2),
        fill=_rgba(acento),
    )
    base = Image.alpha_composite(base, camada)
    return texto_espacado(base, (xy[0] + 42, xy[1]), txt, "negrito", tam, SLATE, 3)


def _etiqueta(base, xy, txt, acento, maximo=560, icone=None, tam=26):
    """Etiqueta de borda fina (título, gênero, patente) com a insígnia opcional à esquerda;
    corta o texto se for longo. `xy` = canto superior esquerdo."""
    x, y = xy
    altura = tam + 26
    lado = altura - 10 if icone else 0
    folga = 16 + (lado + 10 if icone else 0)
    txt = _cortar_largura(limpo(txt).upper(), "semi", tam, maximo - folga - 16, 1.6)
    caixa = (x, y, x + folga + largura(txt, "semi", tam, 1.6) + 16, y + altura)
    camada = _camada(base)
    ImageDraw.Draw(camada).rounded_rectangle(
        [_s(v) for v in caixa],
        radius=_s(altura / 2),
        fill=_rgba(TINTA, 150),
        outline=_rgba(acento, 220),
        width=max(1, _s(1.6)),
    )
    base = Image.alpha_composite(base, camada)
    if icone:
        try:
            img = Image.open(io.BytesIO(icone)).convert("RGBA")
            img.thumbnail((_s(lado), _s(lado)), Image.LANCZOS)
            base.alpha_composite(
                img,
                (
                    _s(x + 10) + (_s(lado) - img.width) // 2,
                    _s(y + 5) + (_s(lado) - img.height) // 2,
                ),
            )
        except Exception:  # noqa: BLE001 - insígnia inválida: só o texto
            pass
    return texto_espacado(
        base, (x + folga, y + altura / 2 + tam * 0.36), txt, "semi", tam, acento, 1.6
    )


def _barra(base, x, y, larg, alt, prop, acento):
    camada = _camada(base)
    d = ImageDraw.Draw(camada)
    d.rounded_rectangle(
        (_s(x), _s(y), _s(x + larg), _s(y + alt)), radius=_s(alt / 2), fill=_rgba(BRANCO, 26)
    )
    prop = max(0.0, min(1.0, prop))
    if prop > 0:
        d.rounded_rectangle(
            (_s(x), _s(y), _s(x + max(alt, larg * prop)), _s(y + alt)),
            radius=_s(alt / 2),
            fill=_rgba(acento),
        )
    return Image.alpha_composite(base, camada)


def _cantos(img, raio):
    mascara = Image.new("L", (img.width * 2, img.height * 2), 0)
    ImageDraw.Draw(mascara).rounded_rectangle(
        (0, 0, img.width * 2 - 1, img.height * 2 - 1), radius=raio * 2, fill=255
    )
    img = img.copy()
    img.putalpha(mascara.resize(img.size, Image.LANCZOS))
    return img


def _selo(base, acento):
    """Assinatura discreta no canto superior direito."""
    w = base.width // SS
    base = texto_espacado(base, (w - 46, 47), "MEMI BOT", "negrito", 22, SLATE, 3, "r")
    camada = _camada(base)
    ImageDraw.Draw(camada).ellipse((_s(w - 34), _s(34), _s(w - 26), _s(42)), fill=_rgba(acento))
    return Image.alpha_composite(base, camada)


def _marca(base, cx, cy, raio, acento):
    """Logo: círculo com 'M', aro fino de vinil e rabinho de balão de fala."""
    C, R = (_s(cx), _s(cy)), _s(raio)
    camada = _camada(base)
    d = ImageDraw.Draw(camada)
    d.polygon(
        [
            (C[0] - R * 0.62, C[1] + R * 0.62),
            (C[0] - R * 0.98, C[1] + R * 1.08),
            (C[0] - R * 0.12, C[1] + R * 0.92),
        ],
        fill=_rgba(acento),
    )
    d.ellipse((C[0] - R, C[1] - R, C[0] + R, C[1] + R), fill=_rgba(acento))
    d.ellipse(
        (C[0] - R * 0.86, C[1] - R * 0.86, C[0] + R * 0.86, C[1] + R * 0.86),
        outline=_rgba(TINTA, 70),
        width=max(1, _s(raio * 0.025)),
    )
    base = Image.alpha_composite(base, camada)
    camada = _camada(base)
    ImageDraw.Draw(camada).text(
        (C[0], C[1] + R * 0.04), "M", font=fonte("forte", raio, "M"), fill=_rgba(TINTA), anchor="mm"
    )
    return Image.alpha_composite(base, camada)


def _rotulo_marca(acento, lado=256):
    """Rótulo do vinil da marca (anéis, sem letra); devolve PNG."""
    img = Image.new("RGBA", (lado, lado), _rgba(acento))
    d = ImageDraw.Draw(img)
    for r in (0.90, 0.66):
        rr = lado * r / 2
        d.ellipse(
            (lado / 2 - rr, lado / 2 - rr, lado / 2 + rr, lado / 2 + rr),
            outline=_rgba(TINTA, 70),
            width=max(2, lado // 90),
        )
    return _png(img)


def _capa_reserva(acento, lado=256):
    """Capa desenhada quando não há capa de álbum: degradê e círculos concêntricos."""
    img = Image.new("RGB", (lado, lado))
    d = ImageDraw.Draw(img)
    for y in range(lado):
        t = y / lado
        d.line(
            (0, y, lado, y),
            fill=tuple(int(acento[i] * (1 - t) + LILAS[i] * t * 0.7) for i in range(3)),
        )
    for k in range(5):
        r = lado * (0.14 + 0.10 * k)
        d.ellipse(
            (lado / 2 - r, lado / 2 - r, lado / 2 + r, lado / 2 + r),
            outline=(255, 255, 255),
            width=3,
        )
    return _png(img)


def _finalizar(base):
    return base.convert("RGB").resize((base.width // SS, base.height // SS), Image.LANCZOS)


def _png(img):
    saida = io.BytesIO()
    img.save(
        saida, "PNG", compress_level=6
    )  # optimize=True custa 10x mais tempo por ~6% de arquivo
    return saida.getvalue()


def _milhar(n):
    return f"{int(n):,}".replace(",", ".")


def _nome(dados, tamanho_max, peso, corpo):
    """Nome de exibição limpo; se não sobrar letra desenhável, usa o nome alternativo ou 'Membro'."""
    nome = limpo(dados.get("nome")) or limpo(dados.get("nome_alt")) or "Membro"
    return ajustar(nome, peso, corpo, tamanho_max)


def _prop(avanco, meta, nivel=1):
    return 1.0 if nivel >= 100 or meta <= 0 else avanco / meta


def _foto(caminho, w, h):
    """Imagem de fundo redimensionada para a escala de desenho, ou None se faltar/for inválida."""
    try:
        return Image.open(caminho).convert("RGBA").resize((_s(w), _s(h)), Image.LANCZOS)
    except Exception:  # noqa: BLE001 - sem o arquivo, o fundo desenhado assume
        return None


def _veu(base, inicio, fim, alfa):
    """Escurece da esquerda para a direita (de `inicio` a `fim` px): contraste sobre a foto."""
    W, H = base.size
    linha = Image.new("L", (W, 1), 0)
    for x in range(W):
        t = (x / SS - inicio) / max(1, fim - inicio)
        linha.putpixel((x, 0), int(alfa * max(0.0, min(1.0, t))))
    veu = Image.new("RGBA", (W, H), _rgba(TINTA))
    veu.putalpha(linha.resize((W, H)))
    return Image.alpha_composite(base, veu)


def _lente(base, lente, avatar, acento):
    """Avatar recortado dentro da lente, com um aro fino na cor de acento."""
    cx, cy, r = lente
    lado = _s(2 * r)
    base.alpha_composite(_avatar_redondo(avatar, lado, acento), (_s(cx - r), _s(cy - r)))
    camada = _camada(base)
    ImageDraw.Draw(camada).ellipse(
        (_s(cx - r), _s(cy - r), _s(cx + r), _s(cy + r)),
        outline=_rgba(acento, 230),
        width=max(1, _s(3)),
    )
    return Image.alpha_composite(base, camada)


def _lente_escura(base, lente):
    """Lente de vidro escuro com um reflexo suave."""
    cx, cy, r = lente
    camada = _camada(base)
    d = ImageDraw.Draw(camada)
    d.ellipse((_s(cx - r), _s(cy - r), _s(cx + r), _s(cy + r)), fill=(14, 14, 20, 245))
    d.arc(
        (_s(cx - r * 0.72), _s(cy - r * 0.72), _s(cx + r * 0.72), _s(cy + r * 0.72)),
        200,
        260,
        fill=_rgba(BRANCO, 60),
        width=_s(5),
    )
    return Image.alpha_composite(base, camada)


def _legenda_xp(nivel, avanco, meta):
    return "nível máximo" if nivel >= 100 else f"{_milhar(avanco)} / {_milhar(meta)} XP"


# ------------------------------------------------------------------ imagens
def gerar_cartao(dados, avatar_bytes=None):
    """Cartão de perfil (1200x400) sobre o fundo fixo, com o avatar na lente dos óculos.
    `dados`: nome, nome_alt, titulo, insignia (PNG ou None), nivel, avanco, meta, patente,
    cor_patente, mensagens, pedidos, roletadas, cor (int RGB ou None)."""
    _exigir()
    acento = _rgb(dados.get("cor"))
    nivel, avanco, meta = (
        int(dados.get("nivel", 1)),
        int(dados.get("avanco", 0)),
        int(dados.get("meta", 1)),
    )
    foto = _foto(FUNDO_CARTAO, 1200, 400)
    if foto is None:
        base = _veu(_fundo(1200, 400, acento), 330, 620, 170)
    else:
        base = _veu(foto, 330, 620, 170)
        # Recoloca só as letras do logo (pixels claros), sem o retângulo do fundo.
        logo = tuple(_s(v) for v in LOGO_CARTAO)
        recorte = foto.crop(logo)
        mascara = recorte.convert("L").point(lambda v: 255 if v > 120 else 0)
        base.paste(recorte, logo[:2], mascara.filter(ImageFilter.GaussianBlur(1)))
    base = _lente(base, LENTE_DIREITA, avatar_bytes, acento)
    base = _lente_escura(base, LENTE_ESQUERDA)
    x, direita = 410, 1160
    nome, corpo = _nome(dados, direita - x, "forte", 62)
    base = texto(base, (x, 106), nome, "forte", corpo)
    patente = limpo(dados.get("patente"))
    chip = limpo(dados.get("titulo")) or patente
    if chip:
        icone = dados.get("insignia") if limpo(dados.get("titulo")) else None
        base = _etiqueta(base, (x, 122), chip, acento, direita - x, icone)
    base = texto_espacado(base, (x, 214), "NÍVEL", "negrito", 24, SLATE, 3)
    base = texto(base, (x - 4, 286), str(nivel), "forte", 76)
    bx = x + largura(str(nivel), "forte", 76) + 28
    if patente:
        cor_patente = _rgb(dados.get("cor_patente") or 0x9AA0A6)
        patente_ok, corpo_p = ajustar(patente, "negrito", 32, direita - bx)
        base = texto(base, (bx, 234), patente_ok, "negrito", corpo_p, cor_patente)
    base = _barra(base, bx, 248, direita - bx, 12, _prop(avanco, meta, nivel), acento)
    base = texto(base, (direita, 286), _legenda_xp(nivel, avanco, meta), "medio", 26, SLATE, "rs")
    colunas = (
        (dados.get("mensagens", 0), "mensagens"),
        (dados.get("pedidos", 0), "músicas"),
        (dados.get("roletadas", 0), "roletadas"),
    )
    for i, (valor, rotulo) in enumerate(colunas):
        cx = x + i * 255
        numero, corpo_n = ajustar(_milhar(valor), "forte", 44, 235)
        base = texto(base, (cx, 342), numero, "forte", corpo_n)
        base = texto(base, (cx, 376), rotulo, "medio", 26, SLATE)
    return _png(_finalizar(base))


def gerar_nivel(dados, avatar_bytes=None):
    """Aviso de level up (1200x400). `dados`: nome, nome_alt, nivel (o anunciado), patente,
    cor_patente, patente_minimo, trocou (nova patente), cor. Aceita também atual/avanco/meta
    (ignorados: a barra fica no texto da mensagem)."""
    _exigir()
    acento = _rgb(dados.get("cor"))
    anunciado = int(dados.get("nivel", 1))
    base = _fundo(1200, 400, acento)
    base = _disco(base, 225, 200, 160, avatar_bytes, acento)
    x = 470
    base = _rotulo(base, (x, 80), "Nova patente" if dados.get("trocou") else "Level up", acento)
    numero = str(anunciado)
    base = texto(base, (x - 6, 236), numero, "forte", 164)
    lar = largura(numero, "forte", 164)
    base = texto_espacado(base, (x + lar + 22, 236), "NÍVEL", "negrito", 36, acento, 5)
    nome, corpo = _nome(dados, 680, "negrito", 44)
    base = texto(base, (x, 298), nome, "negrito", corpo)
    patente = limpo(dados.get("patente"))
    if patente:
        cor_patente = dados.get("cor_patente") or 0x9AA0A6
        emblema = gerar_emblema(
            {"cor": cor_patente, "minimo": dados.get("patente_minimo", "")}, _s(56)
        )
        base.alpha_composite(Image.open(io.BytesIO(emblema)).convert("RGBA"), (_s(x), _s(318)))
        patente_ok, corpo_p = ajustar(patente, "negrito", 34, 600)
        base = texto(base, (x + 70, 360), patente_ok, "negrito", corpo_p, _rgb(cor_patente))
    return _png(_finalizar(_selo(base, acento)))


def gerar_ajuda(cor=None):
    """Banner da ajuda (1200x400): o banner pronto (assets/banners/ajuda.jpg) ou, sem ele ou com
    uma cor pedida, o desenhado. Fica em cache."""
    _exigir()
    return _ajuda_bytes(cor)


@lru_cache(maxsize=8)
def _ajuda_bytes(cor):
    if cor is None:
        pronto = _foto(BANNER_AJUDA, 600, 200)  # 600x200 lógicos = 1200x400 reais
        if pronto is not None:
            return _png(pronto.convert("RGB"))
    acento = _rgb(cor)
    base = _fundo(1200, 400, acento)
    base = _disco(base, 1190, 215, 240, _rotulo_marca(acento), acento, giro=140)
    base = _marca(base, 190, 196, 96, acento)
    x = 370
    base = texto(base, (x, 226), "MeMi", "forte", 118)
    lar = largura("MeMi", "forte", 118)
    base = texto(base, (x + lar + 16, 226), "BOT", "fino", 118, acento)
    base = texto(base, (x + 4, 282), "Resenha, música e ranking.", "medio", 30, SLATE)
    base = _etiqueta(base, (x + 4, 312), "mm!help", acento)
    return _png(_finalizar(base))


def gerar_resumo(dados, avatar_dj=None, avatar_tagarela=None, capa=None):
    """Resumo do mês ou do ano (1200x480). `dados`: tipo ('mes'/'ano'), titulo, ano, dj e tagarela
    ((nome, detalhe) ou None), musica ((titulo, artista, vezes) ou None), mensagens, pedidos, cor.
    """
    _exigir()
    acento = _rgb(dados.get("cor"))
    ano_tipo = dados.get("tipo") == "ano"
    periodo = "ano" if ano_tipo else "mês"
    base = _fundo(1200, 480, acento)
    x = 60
    rotulo = "Resumo do ano" if ano_tipo else f"Resumo · {dados.get('ano', '')}"
    base = _rotulo(base, (x, 88), rotulo, acento, 24)
    titulo, corpo = ajustar(dados.get("titulo") or "—", "forte", 96, 400)
    base = texto(base, (x - 3, 194), titulo, "forte", corpo)
    for i, (valor, legenda) in enumerate(
        ((dados.get("mensagens", "0"), "mensagens"), (dados.get("pedidos", "0"), "músicas"))
    ):
        numero, corpo_n = ajustar(str(valor), "forte", 50, 200)
        base = texto(base, (x + i * 215, 350), numero, "forte", corpo_n)
        base = texto(base, (x + i * 215, 386), legenda, "medio", 26, SLATE)
    ganhadores = (
        (585, dados.get("dj"), "DJ", avatar_dj, acento),
        (800, dados.get("tagarela"), "Resenhex", avatar_tagarela, LILAS),
    )
    for cx, ganhador, rotulo_g, avatar, cor in ganhadores:
        nome, detalhe = ganhador if ganhador else ("—", "")
        nome = limpo(nome) or "Membro"
        base = _disco(base, cx, 160, 82, avatar, cor)
        base = texto_espacado(base, (cx, 290), rotulo_g, "negrito", 24, cor, 3, "m")
        nome_ok, corpo_n = ajustar(nome, "negrito", 32, 205)
        base = texto(base, (cx, 334), nome_ok, "negrito", corpo_n, BRANCO, "ms")
        detalhe_ok, corpo_d = ajustar(detalhe, "medio", 26, 205)
        base = texto(base, (cx, 370), detalhe_ok, "medio", corpo_d, SLATE, "ms")
    lado = 200
    try:
        cover = Image.open(io.BytesIO(capa)).convert("RGBA")
    except Exception:  # noqa: BLE001 - sem capa ou capa inválida
        cover = Image.open(io.BytesIO(_capa_reserva(acento))).convert("RGBA")
    cover = _cantos(cover.resize((_s(lado), _s(lado)), Image.LANCZOS), _s(14))
    cx0 = 920
    base = _sombra_caixa(base, (cx0, 70, cx0 + lado, 70 + lado), 14)
    base.alpha_composite(cover, (_s(cx0), _s(70)))
    base = texto_espacado(base, (cx0, 310), "MÚSICA", "negrito", 24, acento, 3)
    musica = dados.get("musica")
    if musica:
        faixa, artista, vezes = musica
        faixa_ok, corpo_f = ajustar(faixa, "negrito", 32, 240)
        base = texto(base, (cx0, 350), faixa_ok, "negrito", corpo_f)
        artista_ok, corpo_a = ajustar(f"{artista} · {vezes}x", "medio", 26, 240)
        base = texto(base, (cx0, 386), artista_ok, "medio", corpo_a, SLATE)
    else:
        base = texto(base, (cx0, 350), "Sem músicas", "negrito", 32)
    return _png(_finalizar(_selo(base, acento)))


def gerar_emblema(patente, lado=128):
    """Emblema quadrado (PNG com transparência) de uma patente, para virar emoji: escudo na cor da
    patente com o nível mínimo dentro. Sem ruído aleatório: a mesma patente gera sempre os
    mesmos bytes (o emoji só é reenviado quando o desenho muda)."""
    _exigir()
    cor = _rgb(patente.get("cor"))
    escuro = tuple(int(c * 0.45) for c in cor)
    claro = tuple(min(255, int(c + (255 - c) * 0.35)) for c in cor)
    L = lado * 4
    img = Image.new("RGBA", (L, L), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def escudo(margem):
        m = L * margem
        topo, base = m, L - m
        return [
            (L / 2, topo),
            (L - m, topo + L * 0.14),
            (L - m, L * 0.52),
            (L / 2, base),
            (m, L * 0.52),
            (m, topo + L * 0.14),
        ]

    d.polygon(escudo(0.04), fill=_rgba(claro))
    d.polygon(escudo(0.09), fill=_rgba(cor))
    d.polygon(escudo(0.16), fill=_rgba(escuro))
    numero = str(patente.get("minimo", ""))
    corpo = 0.40 if len(numero) < 3 else 0.30
    d.text(
        (L / 2, L * 0.47),
        numero,
        font=fonte("forte", L * corpo / SS, numero),
        fill=_rgba(BRANCO),
        anchor="mm",
    )
    img = img.resize((lado, lado), Image.LANCZOS)
    saida = io.BytesIO()
    img.save(saida, "PNG", compress_level=6)
    return saida.getvalue()


def gerar_wrapped(dados, avatar_bytes=None):
    """Wrapped individual (1200x630). `dados`: nome, nome_alt, meses (12 contagens), pedidos, dias,
    genero, top ([(titulo, vezes)]), cor."""
    _exigir()
    acento = _rgb(dados.get("cor"))
    base = _fundo(1200, 630, acento)
    base = _disco(base, 200, 186, 128, avatar_bytes, acento)
    base = _rotulo(base, (60, 384), "Wrapped", acento, 24)
    nome, corpo = _nome(dados, 340, "negrito", 36)
    base = texto(base, (60, 432), nome, "negrito", corpo)
    base = texto(base, (60, 468), "últimos 12 meses", "medio", 26, SLATE)
    x = 450
    base = texto(base, (x, 150), _milhar(dados.get("pedidos", 0)), "forte", 116)
    base = texto(
        base, (x + 4, 196), f"músicas pedidas em {dados.get('dias', 0)} dias", "medio", 30, SLATE
    )
    if limpo(dados.get("genero")):
        base = _etiqueta(base, (x + 4, 216), f"gênero · {dados['genero']}", LILAS, 680)
    base = _rotulo(base, (x + 4, 318), "Mais pedidas", acento, 24)
    for i, (titulo, vezes) in enumerate(list(dados.get("top") or [])[:3], start=1):
        y = 364 + (i - 1) * 44
        base = texto(base, (x + 4, y), f"0{i}", "negrito", 28, acento)
        titulo_ok, corpo_t = ajustar(titulo, "semi", 30, 560)
        base = texto(base, (x + 56, y), titulo_ok, "semi", corpo_t)
        base = texto(base, (1140, y), f"{vezes}x", "medio", 28, SLATE, "rs")
    meses = (list(dados.get("meses") or []) + [0] * 12)[:12]
    maior = max(meses) or 1
    x0, base_y, larg, folga = 60, 580, 50, 42
    camada = _camada(base)
    d = ImageDraw.Draw(camada)
    for i, v in enumerate(meses):
        alt = max(6, 64 * v / maior)
        destaque = v == maior and v > 0
        d.rounded_rectangle(
            (
                _s(x0 + i * (larg + folga)),
                _s(base_y - alt),
                _s(x0 + i * (larg + folga) + larg),
                _s(base_y),
            ),
            radius=_s(6),
            fill=_rgba(acento if destaque else LILAS, 255 if destaque else 110),
        )
    base = Image.alpha_composite(base, camada)
    for i, letra in enumerate("JFMAMJJASOND"):
        base = texto(
            base, (x0 + i * (larg + folga) + larg / 2, 618), letra, "semi", 24, SLATE, "ms"
        )
    return _png(_finalizar(_selo(base, acento)))
