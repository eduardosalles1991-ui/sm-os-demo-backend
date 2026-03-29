"""
relatorio_pdf.py — Gerador de Relatório Jurídico PDF
S&M OS 6.1 — Salles & Mendes
Uso: importar build_relatorio_pdf() no main.py
"""
import io
from datetime import datetime
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle, KeepTogether,
)

# ── Paleta S&M ────────────────────────────────────────────
GOLD      = colors.HexColor("#C8A84B")
GOLD_LIGHT= colors.HexColor("#F0D999")
DARK_BG   = colors.HexColor("#0D1520")
DARK_MID  = colors.HexColor("#1A2535")
DARK_LINE = colors.HexColor("#2A3A50")
WHITE     = colors.white
GRAY_TEXT = colors.HexColor("#8A9AB8")
RED_RISK  = colors.HexColor("#C0392B")
GREEN_OK  = colors.HexColor("#1E8449")
ORANGE    = colors.HexColor("#D35400")

# ── Estilos ───────────────────────────────────────────────
def _styles():
    base = getSampleStyleSheet()

    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    return {
        "capa_titulo": ps("capa_titulo",
            fontName="Helvetica-Bold", fontSize=22, textColor=GOLD,
            alignment=TA_CENTER, spaceAfter=6, leading=28),
        "capa_sub": ps("capa_sub",
            fontName="Helvetica", fontSize=11, textColor=WHITE,
            alignment=TA_CENTER, spaceAfter=4),
        "capa_meta": ps("capa_meta",
            fontName="Helvetica", fontSize=9, textColor=GRAY_TEXT,
            alignment=TA_CENTER, spaceAfter=3),
        "section": ps("section",
            fontName="Helvetica-Bold", fontSize=11, textColor=GOLD,
            spaceBefore=14, spaceAfter=4, leading=14,
            borderPad=(0, 0, 2, 0)),
        "subsection": ps("subsection",
            fontName="Helvetica-Bold", fontSize=9.5, textColor=WHITE,
            spaceBefore=8, spaceAfter=3, leading=12),
        "body": ps("body",
            fontName="Helvetica", fontSize=9, textColor=WHITE,
            leading=13, spaceAfter=3, alignment=TA_JUSTIFY),
        "body_small": ps("body_small",
            fontName="Helvetica", fontSize=8, textColor=GRAY_TEXT,
            leading=11, spaceAfter=2),
        "bullet": ps("bullet",
            fontName="Helvetica", fontSize=9, textColor=WHITE,
            leading=12, spaceAfter=2, leftIndent=12,
            bulletIndent=0, bulletText="•"),
        "label": ps("label",
            fontName="Helvetica-Bold", fontSize=8.5, textColor=GOLD_LIGHT,
            spaceAfter=1),
        "value": ps("value",
            fontName="Helvetica", fontSize=9, textColor=WHITE,
            spaceAfter=4, leading=12),
        "score_alto": ps("score_alto",
            fontName="Helvetica-Bold", fontSize=10, textColor=GREEN_OK,
            alignment=TA_CENTER),
        "score_medio": ps("score_medio",
            fontName="Helvetica-Bold", fontSize=10, textColor=ORANGE,
            alignment=TA_CENTER),
        "score_baixo": ps("score_baixo",
            fontName="Helvetica-Bold", fontSize=10, textColor=RED_RISK,
            alignment=TA_CENTER),
        "aviso": ps("aviso",
            fontName="Helvetica-Oblique", fontSize=7.5, textColor=GRAY_TEXT,
            alignment=TA_CENTER, leading=10),
        "rodape": ps("rodape",
            fontName="Helvetica", fontSize=7, textColor=GRAY_TEXT,
            alignment=TA_CENTER),
    }

# ── Header / Footer ───────────────────────────────────────
def _make_header_footer(mandataria_nome: str, mandataria_oab: str):
    def on_page(canvas, doc):
        canvas.saveState()
        w, h = A4

        # Header bar
        canvas.setFillColor(DARK_MID)
        canvas.rect(0, h - 28*mm, w, 28*mm, fill=1, stroke=0)
        canvas.setFillColor(GOLD)
        canvas.rect(0, h - 29.5*mm, w, 1.5*mm, fill=1, stroke=0)

        # Logo text
        canvas.setFont("Helvetica-Bold", 12)
        canvas.setFillColor(GOLD)
        canvas.drawString(1.5*cm, h - 15*mm, "S&M")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(WHITE)
        canvas.drawString(2.6*cm, h - 12*mm, "SALLES & MENDES")
        canvas.setFillColor(GRAY_TEXT)
        canvas.drawString(2.6*cm, h - 17*mm, "Sistema Operacional Jurídico OS 6.1")

        # Escritório info (direita)
        if mandataria_nome:
            canvas.setFont("Helvetica-Bold", 7.5)
            canvas.setFillColor(WHITE)
            canvas.drawRightString(w - 1.5*cm, h - 12*mm, mandataria_nome)
        if mandataria_oab:
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(GRAY_TEXT)
            canvas.drawRightString(w - 1.5*cm, h - 17*mm, mandataria_oab)

        # Footer
        canvas.setFillColor(DARK_MID)
        canvas.rect(0, 0, w, 18*mm, fill=1, stroke=0)
        canvas.setFillColor(GOLD)
        canvas.rect(0, 18*mm, w, 0.8*mm, fill=1, stroke=0)

        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(GRAY_TEXT)
        aviso = (
            "DOCUMENTO CONFIDENCIAL — Uso exclusivo interno do escritório. "
            "Esta análise é assistiva e não substitui o julgamento do advogado responsável. "
            "Não constitui promessa de resultado. OS 6.1 / LGPD / OAB."
        )
        canvas.drawCentredString(w/2, 11*mm, aviso)

        # Número de página
        canvas.setFont("Helvetica-Bold", 7)
        canvas.setFillColor(GOLD)
        canvas.drawCentredString(w/2, 6*mm, f"Página {doc.page}")

        canvas.restoreState()

    return on_page

# ── Helpers de construção ────────────────────────────────
def _hr(story, color=DARK_LINE):
    story.append(HRFlowable(width="100%", thickness=0.5, color=color, spaceAfter=4, spaceBefore=4))

def _section(story, title: str, s):
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"▸  {title.upper()}", s["section"]))
    _hr(story, GOLD)

def _kv(story, label: str, value: str, s):
    if value:
        story.append(Paragraph(label, s["label"]))
        story.append(Paragraph(str(value), s["value"]))

def _score_style(score: int, s) -> str:
    if score >= 70: return "score_alto"
    if score >= 40: return "score_medio"
    return "score_baixo"

def _score_table(scores: Dict[str, int], s) -> Table:
    labels = {
        "score_viabilidade":        "Viabilidade",
        "score_risco":              "Risco",
        "score_rentabilidade":      "Rentabilidade",
        "score_urgencia":           "Urgência",
        "score_prioridade_carteira":"Prioridade",
        "score_composto_priorizacao":"Score Geral",
    }
    header = [Paragraph("<b>Critério</b>", ParagraphStyle("h", fontName="Helvetica-Bold",
              fontSize=8, textColor=GOLD, alignment=TA_CENTER))]
    vals   = [Paragraph("<b>Score</b>", ParagraphStyle("h", fontName="Helvetica-Bold",
              fontSize=8, textColor=GOLD, alignment=TA_CENTER))]

    rows = [[
        Paragraph("CRITÉRIO", ParagraphStyle("th", fontName="Helvetica-Bold",
                  fontSize=8, textColor=GOLD, alignment=TA_CENTER)),
        Paragraph("PONTUAÇÃO", ParagraphStyle("th", fontName="Helvetica-Bold",
                  fontSize=8, textColor=GOLD, alignment=TA_CENTER)),
        Paragraph("NÍVEL", ParagraphStyle("th", fontName="Helvetica-Bold",
                  fontSize=8, textColor=GOLD, alignment=TA_CENTER)),
    ]]

    for key, label in labels.items():
        val = scores.get(key, 0)
        if val is None: val = 0
        nivel = "ALTO" if val >= 70 else ("MÉDIO" if val >= 40 else "BAIXO")
        cor   = GREEN_OK if val >= 70 else (ORANGE if val >= 40 else RED_RISK)
        rows.append([
            Paragraph(label, ParagraphStyle("td", fontName="Helvetica", fontSize=8.5, textColor=WHITE)),
            Paragraph(str(val), ParagraphStyle("td", fontName="Helvetica-Bold", fontSize=10,
                      textColor=cor, alignment=TA_CENTER)),
            Paragraph(nivel, ParagraphStyle("td", fontName="Helvetica-Bold", fontSize=8,
                      textColor=cor, alignment=TA_CENTER)),
        ])

    t = Table(rows, colWidths=[8*cm, 3*cm, 3*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  DARK_MID),
        ("BACKGROUND",    (0,1), (-1,-1), DARK_BG),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [DARK_BG, DARK_MID]),
        ("GRID",          (0,0), (-1,-1), 0.3, DARK_LINE),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
    ]))
    return t

def _mov_table(movimentos: List[Dict], s) -> Table:
    rows = [[
        Paragraph("DATA", ParagraphStyle("th", fontName="Helvetica-Bold",
                  fontSize=7.5, textColor=GOLD, alignment=TA_CENTER)),
        Paragraph("MOVIMENTAÇÃO", ParagraphStyle("th", fontName="Helvetica-Bold",
                  fontSize=7.5, textColor=GOLD)),
        Paragraph("TIPO", ParagraphStyle("th", fontName="Helvetica-Bold",
                  fontSize=7.5, textColor=GOLD, alignment=TA_CENTER)),
    ]]
    for mov in movimentos[:30]:
        data = (mov.get("dataHora") or "")[:10]
        nome = mov.get("nome") or "Movimentação"
        tipo = mov.get("_tipo", "")
        rows.append([
            Paragraph(data, ParagraphStyle("td", fontName="Helvetica", fontSize=7.5,
                      textColor=GRAY_TEXT, alignment=TA_CENTER)),
            Paragraph(nome[:80], ParagraphStyle("td", fontName="Helvetica", fontSize=7.5,
                      textColor=WHITE, leading=10)),
            Paragraph(tipo, ParagraphStyle("td", fontName="Helvetica", fontSize=7,
                      textColor=GOLD_LIGHT, alignment=TA_CENTER)),
        ])
    t = Table(rows, colWidths=[2.5*cm, 10*cm, 2.5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  DARK_MID),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [DARK_BG, DARK_MID]),
        ("GRID",          (0,0), (-1,-1), 0.3, DARK_LINE),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
    ]))
    return t

# ── BUILDER PRINCIPAL ────────────────────────────────────
def build_relatorio_pdf(
    processo: Dict[str, Any],
    analise_os61: Dict[str, Any],
    mandataria_nome: str = "",
    mandataria_oab: str = "",
) -> bytes:
    """
    Gera o PDF do relatório jurídico OS 6.1.

    Args:
        processo:      dict normalizado do DataJud (DJ.normalize())
        analise_os61:  dict com a análise estruturada do GPT/OS 6.1
        mandataria_nome / oab: dados do escritório

    Returns:
        bytes do PDF
    """
    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=3.5*cm, bottomMargin=2.5*cm,
    )
    s      = _styles()
    story  = []
    on_pg  = _make_header_footer(mandataria_nome, mandataria_oab)

    numero = processo.get("numero_processo") or "N/D"
    data_rel = datetime.now().strftime("%d/%m/%Y às %H:%M")

    # ════════════════════════════════════════════════════
    # CAPA
    # ════════════════════════════════════════════════════
    story.append(Spacer(1, 2*cm))
    story.append(Paragraph("RELATÓRIO JURÍDICO", s["capa_titulo"]))
    story.append(Paragraph("ANÁLISE ESTRATÉGICA DE PROCESSO", s["capa_sub"]))
    story.append(Spacer(1, 0.5*cm))

    # Box do processo
    capa_data = [
        [Paragraph("PROCESSO", ParagraphStyle("cl", fontName="Helvetica-Bold",
                   fontSize=8, textColor=GOLD, alignment=TA_CENTER)),
         Paragraph("TRIBUNAL", ParagraphStyle("cl", fontName="Helvetica-Bold",
                   fontSize=8, textColor=GOLD, alignment=TA_CENTER)),
         Paragraph("DATA DO RELATÓRIO", ParagraphStyle("cl", fontName="Helvetica-Bold",
                   fontSize=8, textColor=GOLD, alignment=TA_CENTER))],
        [Paragraph(numero, ParagraphStyle("cv", fontName="Helvetica-Bold",
                   fontSize=8.5, textColor=WHITE, alignment=TA_CENTER)),
         Paragraph(processo.get("tribunal") or processo.get("orgao_julgador") or "N/D",
                   ParagraphStyle("cv", fontName="Helvetica", fontSize=8.5,
                                  textColor=WHITE, alignment=TA_CENTER)),
         Paragraph(data_rel, ParagraphStyle("cv", fontName="Helvetica", fontSize=8.5,
                                            textColor=WHITE, alignment=TA_CENTER))],
    ]
    capa_t = Table(capa_data, colWidths=[7*cm, 5*cm, 5*cm])
    capa_t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0),  DARK_MID),
        ("BACKGROUND",   (0,1), (-1,1),  DARK_BG),
        ("GRID",         (0,0), (-1,-1), 0.5, GOLD),
        ("TOPPADDING",   (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0), (-1,-1), 8),
    ]))
    story.append(capa_t)
    story.append(Spacer(1, 0.4*cm))

    # Classificação rápida (badges)
    classif = analise_os61.get("classificacao", {})
    forca   = analise_os61.get("forca_tese", "N/D")
    conf    = analise_os61.get("confiabilidade", "N/D")
    badge_data = [[
        Paragraph(f"FORÇA DA TESE\n{forca}", ParagraphStyle("b", fontName="Helvetica-Bold",
                  fontSize=9, textColor=GOLD_LIGHT, alignment=TA_CENTER, leading=13)),
        Paragraph(f"CONFIABILIDADE\n{conf}", ParagraphStyle("b", fontName="Helvetica-Bold",
                  fontSize=9, textColor=GOLD_LIGHT, alignment=TA_CENTER, leading=13)),
        Paragraph(f"ÁREA\n{classif.get('area','N/D')}", ParagraphStyle("b", fontName="Helvetica-Bold",
                  fontSize=9, textColor=GOLD_LIGHT, alignment=TA_CENTER, leading=13)),
        Paragraph(f"FASE\n{classif.get('fase','N/D')}", ParagraphStyle("b", fontName="Helvetica-Bold",
                  fontSize=9, textColor=GOLD_LIGHT, alignment=TA_CENTER, leading=13)),
    ]]
    badge_t = Table(badge_data, colWidths=[4*cm, 4*cm, 4*cm, 4*cm])
    badge_t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), DARK_MID),
        ("GRID",         (0,0),(-1,-1), 0.3, DARK_LINE),
        ("TOPPADDING",   (0,0),(-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("ROUNDEDCORNERS", [3]),
    ]))
    story.append(badge_t)
    story.append(PageBreak())

    # ════════════════════════════════════════════════════
    # 1. DADOS DO PROCESSO
    # ════════════════════════════════════════════════════
    _section(story, "1. Dados do Processo", s)

    partes = processo.get("partes") or []
    partes_txt = "Não disponível via DataJud (requer MNI/PJe)"
    if partes:
        linhas = []
        for p in partes:
            tipo  = p.get("tipo","")
            nomes = ", ".join(p.get("nomes") or [])
            linhas.append(f"{tipo}: {nomes}")
        partes_txt = " | ".join(linhas)

    advs = processo.get("advogados") or []
    advs_txt = "Não disponível via DataJud" if not advs else \
               " | ".join(f"{a.get('nome','')} (OAB: {a.get('oab','n/d')})" for a in advs)

    grid_data = [
        ["Número do Processo", numero,
         "Tribunal", processo.get("tribunal") or "N/D"],
        ["Vara / Órgão Julgador", processo.get("orgao_julgador") or "N/D",
         "Grau", processo.get("grau") or "N/D"],
        ["Classe Processual", processo.get("classe_nome") or "N/D",
         "Data de Ajuizamento", processo.get("data_ajuizamento") or "N/D"],
        ["Magistrado", processo.get("magistrado") or "Não disponível via DataJud",
         "Fonte do Magistrado", processo.get("magistrado_fonte") or "N/D"],
        ["Valor da Causa", processo.get("valor_causa") or "N/D",
         "Última Atualização", processo.get("ultima_atualizacao") or "N/D"],
        ["Assuntos", ", ".join(processo.get("assuntos") or []) or "N/D", "", ""],
        ["Partes", partes_txt, "", ""],
        ["Advogados", advs_txt, "", ""],
    ]

    def _gcell(txt, bold=False):
        fn = "Helvetica-Bold" if bold else "Helvetica"
        return Paragraph(str(txt), ParagraphStyle("gc", fontName=fn,
                         fontSize=8, textColor=WHITE if not bold else GOLD_LIGHT, leading=11))

    grid_rows = []
    for row in grid_data:
        if len(row) == 4 and row[2]:
            grid_rows.append([_gcell(row[0], True), _gcell(row[1]),
                               _gcell(row[2], True), _gcell(row[3])])
        else:
            grid_rows.append([_gcell(row[0], True), _gcell(row[1]), "", ""])

    gt = Table(grid_rows, colWidths=[4*cm, 7*cm, 3*cm, 3*cm])
    gt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), DARK_BG),
        ("ROWBACKGROUNDS",(0,0), (-1,-1), [DARK_BG, DARK_MID]),
        ("GRID",          (0,0), (-1,-1), 0.3, DARK_LINE),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 7),
        ("SPAN",          (1,5), (3,5)),
        ("SPAN",          (1,6), (3,6)),
        ("SPAN",          (1,7), (3,7)),
    ]))
    story.append(gt)

    # ════════════════════════════════════════════════════
    # 2. SÍNTESE E ANÁLISE TÉCNICA
    # ════════════════════════════════════════════════════
    _section(story, "2. Síntese e Análise Técnica", s)

    sintese = analise_os61.get("sintese", "")
    if sintese:
        story.append(Paragraph(sintese, s["body"]))
        story.append(Spacer(1, 0.2*cm))

    analise = analise_os61.get("analise_tecnica", "")
    if analise:
        story.append(Paragraph("Análise Técnica", s["subsection"]))
        story.append(Paragraph(analise, s["body"]))

    questao = analise_os61.get("questao_juridica", "")
    if questao:
        story.append(Paragraph("Questão Jurídica Central", s["subsection"]))
        story.append(Paragraph(questao, s["body"]))

    # ════════════════════════════════════════════════════
    # 3. SCORES
    # ════════════════════════════════════════════════════
    scores = analise_os61.get("scores", {})
    if scores:
        _section(story, "3. Matriz de Scores (0–100)", s)
        story.append(_score_table(scores, s))
        criterios = analise_os61.get("criterios_scores", {})
        if criterios:
            story.append(Spacer(1, 0.2*cm))
            for k, v in criterios.items():
                if v:
                    story.append(Paragraph(f"<b>{k}:</b> {v}", s["body_small"]))

    # ════════════════════════════════════════════════════
    # 4. RISCOS
    # ════════════════════════════════════════════════════
    riscos = analise_os61.get("riscos", [])
    if riscos:
        _section(story, "4. Riscos Identificados", s)
        for r in riscos:
            nivel = (r.get("nivel") or "").upper()
            cor_nivel = {"CRÍTICO": RED_RISK, "ALTO": RED_RISK,
                         "MÉDIO": ORANGE, "BAIXO": GREEN_OK}.get(nivel, WHITE)
            story.append(KeepTogether([
                Paragraph(
                    f'<font color="#{cor_nivel.hexval()[2:]}"><b>[{nivel}]</b></font> '
                    f'{r.get("descricao","") or r.get("risco","")}',
                    s["bullet"]
                ),
            ]))

    # ════════════════════════════════════════════════════
    # 5. RED TEAM
    # ════════════════════════════════════════════════════
    red_team = analise_os61.get("red_team", {})
    if red_team:
        _section(story, "5. Red Team — Argumentos da Parte Contrária", s)

        ataques = red_team.get("ataques", [])
        if ataques:
            story.append(Paragraph("Ataques Prováveis", s["subsection"]))
            for a in ataques:
                story.append(Paragraph(str(a), s["bullet"]))

        vulneravel = red_team.get("ponto_mais_vulneravel", "")
        if vulneravel:
            story.append(Paragraph("Ponto Mais Vulnerável", s["subsection"]))
            story.append(Paragraph(vulneravel, s["body"]))

        preventivas = red_team.get("medidas_preventivas", [])
        if preventivas:
            story.append(Paragraph("Medidas Preventivas", s["subsection"]))
            for p in preventivas:
                story.append(Paragraph(str(p), s["bullet"]))

        indeferimentos = red_team.get("pontos_indeferimento", [])
        if indeferimentos:
            story.append(Paragraph("Pontos de Potencial Indeferimento", s["subsection"]))
            for i in indeferimentos:
                story.append(Paragraph(str(i), s["bullet"]))

    # ════════════════════════════════════════════════════
    # 6. ESTRATÉGIA E AÇÕES PRIORITÁRIAS
    # ════════════════════════════════════════════════════
    estrategia = analise_os61.get("estrategia", {})
    if estrategia:
        _section(story, "6. Estratégia e Ações Prioritárias", s)

        linha = estrategia.get("linha_principal", "")
        if linha:
            story.append(Paragraph("Linha Principal", s["subsection"]))
            story.append(Paragraph(linha, s["body"]))

        subsidiarias = estrategia.get("linhas_subsidiarias", [])
        if subsidiarias:
            story.append(Paragraph("Linhas Subsidiárias", s["subsection"]))
            for ls in subsidiarias:
                story.append(Paragraph(str(ls), s["bullet"]))

        acoes = estrategia.get("acoes_prioritarias", [])
        if acoes:
            story.append(Paragraph("Ações Prioritárias (Curto Prazo)", s["subsection"]))
            for i, a in enumerate(acoes, 1):
                story.append(Paragraph(f"{i}. {a}", s["body"]))

        pendencias = analise_os61.get("pendencias", [])
        if pendencias:
            story.append(Paragraph("Pendências", s["subsection"]))
            for p in pendencias:
                story.append(Paragraph(str(p), s["bullet"]))

    # ════════════════════════════════════════════════════
    # 7. MOVIMENTAÇÕES DO PROCESSO
    # ════════════════════════════════════════════════════
    movimentos = processo.get("movimentos_todos") or []
    if movimentos:
        _section(story, f"7. Movimentações ({len(movimentos)} registros — últimas 30)", s)

        # Adicionar tipo em cada movimento
        for mov in movimentos[:30]:
            tipo_map = {"sentenca":"Sentença","acordao":"Acórdão",
                        "decisao_interlocutoria":"Dec. Interl.","outro":""}
            # Tenta usar classify_mov se disponível
            try:
                from main_v6 import DJ
                mov["_tipo"] = tipo_map.get(DJ.classify_mov(mov), "")
            except Exception:
                mov["_tipo"] = ""

        story.append(_mov_table(movimentos, s))

    # ════════════════════════════════════════════════════
    # 8. ALERTAS
    # ════════════════════════════════════════════════════
    alertas = analise_os61.get("alertas", [])
    if alertas:
        _section(story, "8. Alertas", s)
        for alerta in alertas:
            nivel = (alerta.get("nivel_risco") or alerta.get("nivel") or "").upper()
            desc  = alerta.get("descricao") or alerta.get("tipo") or str(alerta)
            acao  = alerta.get("acao_recomendada", "")
            cor   = {"CRÍTICO": RED_RISK, "ALTO": RED_RISK,
                     "MÉDIO": ORANGE, "BAIXO": GREEN_OK}.get(nivel, WHITE)
            story.append(Paragraph(
                f'<font color="#{cor.hexval()[2:]}"><b>[{nivel}]</b></font> {desc}'
                + (f' — <i>{acao}</i>' if acao else ""),
                s["bullet"]
            ))

    # ════════════════════════════════════════════════════
    # AVISO LEGAL FINAL
    # ════════════════════════════════════════════════════
    story.append(Spacer(1, 0.5*cm))
    _hr(story, GOLD)
    story.append(Paragraph(
        "AVISO LEGAL: Este relatório foi gerado pelo Sistema Operacional Jurídico OS 6.1 "
        "e tem caráter exclusivamente assistivo. Não substitui o julgamento do advogado "
        "responsável, não constitui orientação jurídica definitiva e não implica promessa "
        "de resultado. Todas as análises, scores e classificações são ferramentas de suporte "
        "à decisão e devem ser validadas pelo profissional habilitado. "
        f"Gerado em {data_rel} | Compliance OAB | LGPD",
        s["aviso"]
    ))

    # Build
    doc.build(story, onFirstPage=on_pg, onLaterPages=on_pg)
    buf.seek(0)
    return buf.read()


# ── Parser de análise GPT → dict estruturado ────────────
def parse_analise_gpt(texto_gpt: str) -> Dict[str, Any]:
    """
    Converte a resposta em texto do GPT/OS 6.1 em dict estruturado
    para alimentar o build_relatorio_pdf().
    Extrai seções pelo padrão de output do OS 6.1.
    """
    txt  = texto_gpt or ""
    result: Dict[str, Any] = {
        "sintese": "", "analise_tecnica": "", "questao_juridica": "",
        "forca_tese": "N/D", "confiabilidade": "N/D",
        "classificacao": {"area":"Trabalhista","fase":"N/D"},
        "scores": {}, "criterios_scores": {},
        "riscos": [], "red_team": {}, "estrategia": {},
        "pendencias": [], "alertas": [],
    }

    def _extract(label: str) -> str:
        """Extrai conteúdo após um label até o próximo bloco."""
        patterns = [
            rf"(?i){re.escape(label)}[:\s*#\-]*\n(.*?)(?=\n[A-Z\d]{{1,3}}[\.\):\s]{{1,3}}[A-ZÁÉÍÓÚÀÂÊÔÃÕÜ]|\Z)",
            rf"(?i)\*\*{re.escape(label)}\*\*[:\s]*(.*?)(?=\*\*[A-Z]|\Z)",
            rf"(?i)#{1,3}\s*{re.escape(label)}[:\s]*(.*?)(?=#{1,3}\s*[A-Z]|\Z)",
        ]
        for pat in patterns:
            m = re.search(pat, txt, re.DOTALL)
            if m:
                return m.group(1).strip()[:1200]
        return ""

    def _extract_list(label: str) -> List[str]:
        block = _extract(label)
        if not block:
            return []
        items = []
        for line in block.split("\n"):
            line = re.sub(r"^[\-\*\d\.\)]\s*", "", line).strip()
            if line and len(line) > 5:
                items.append(line)
        return items[:10]

    # Síntese
    result["sintese"] = _extract("SÍNTESE") or _extract("SÍNTESE DO CASO") or _extract("RESUMO")

    # Análise técnica
    result["analise_tecnica"] = _extract("ANÁLISE TÉCNICA") or _extract("ANÁLISE JURÍDICA")

    # Questão jurídica
    result["questao_juridica"] = _extract("QUESTÃO JURÍDICA") or _extract("QUESTÃO CENTRAL")

    # Força da tese
    for label in ["FORÇA DA TESE","FORCA DA TESE","CLASSIFICAÇÃO DA TESE"]:
        v = _extract(label)
        if v:
            for nivel in ["MUITO FORTE","FORTE","MODERADA","FRACA","MUITO FRACA"]:
                if nivel in v.upper():
                    result["forca_tese"] = nivel.title()
                    break
            else:
                result["forca_tese"] = v[:30]
            break

    # Confiabilidade
    for label in ["CONFIABILIDADE","NÍVEL DE CONFIANÇA"]:
        v = _extract(label)
        if v:
            for nivel in ["ALTA","MÉDIA","BAIXA"]:
                if nivel in v.upper():
                    result["confiabilidade"] = nivel.title()
                    break
            break

    # Scores — tenta extrair números
    score_keys = {
        "viabilidade":  "score_viabilidade",
        "risco":        "score_risco",
        "rentabilidade":"score_rentabilidade",
        "urgência":     "score_urgencia",
        "urgencia":     "score_urgencia",
        "prioridade":   "score_prioridade_carteira",
        "composto":     "score_composto_priorizacao",
    }
    for kw, sk in score_keys.items():
        m = re.search(rf"(?i){kw}[^\d]{{0,20}}(\d{{1,3}})", txt)
        if m:
            result["scores"][sk] = min(100, int(m.group(1)))

    # Riscos
    riscos_block = _extract("RISCOS") or _extract("RISCOS JURÍDICOS") or _extract("RISCOS IDENTIFICADOS")
    if riscos_block:
        for line in riscos_block.split("\n"):
            line = re.sub(r"^[\-\*\d\.\)]\s*", "", line).strip()
            if len(line) > 8:
                nivel = "MÉDIO"
                for n in ["CRÍTICO","ALTO","MÉDIO","BAIXO"]:
                    if n in line.upper():
                        nivel = n; break
                result["riscos"].append({"nivel": nivel, "descricao": line[:200]})

    # Red team
    rt_block = _extract("RED TEAM") or _extract("ARGUMENTOS DA PARTE CONTRÁRIA")
    if rt_block:
        result["red_team"] = {
            "ataques":              _extract_list("ATAQUES") or [l.strip() for l in rt_block.split("\n") if len(l.strip()) > 10][:5],
            "ponto_mais_vulneravel":_extract("PONTO MAIS VULNERÁVEL") or _extract("PONTO VULNERÁVEL"),
            "medidas_preventivas":  _extract_list("MEDIDAS PREVENTIVAS"),
            "pontos_indeferimento": _extract_list("INDEFERIMENTO"),
        }

    # Estratégia
    estr_block = _extract("ESTRATÉGIA") or _extract("ESTRATEGIA")
    if estr_block:
        result["estrategia"] = {
            "linha_principal":    _extract("LINHA PRINCIPAL") or estr_block[:300],
            "linhas_subsidiarias":_extract_list("LINHAS SUBSIDIÁRIAS"),
            "acoes_prioritarias": _extract_list("AÇÕES PRIORITÁRIAS") or _extract_list("AÇÕES"),
        }

    # Pendências
    result["pendencias"] = _extract_list("PENDÊNCIAS") or _extract_list("PENDENCIAS")

    # Alertas
    alertas_block = _extract("ALERTAS") or ""
    for line in alertas_block.split("\n"):
        line = re.sub(r"^[\-\*\d\.\)]\s*","",line).strip()
        if len(line) > 8:
            nivel = "MÉDIO"
            for n in ["CRÍTICO","ALTO","MÉDIO","BAIXO"]:
                if n in line.upper():
                    nivel = n; break
            result["alertas"].append({"nivel_risco": nivel, "descricao": line[:200]})

    return result
