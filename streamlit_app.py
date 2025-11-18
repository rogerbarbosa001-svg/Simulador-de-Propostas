import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm
import io
import math
from urllib.parse import quote
from typing import Tuple, Dict, Any
import datetime # Importado para pegar o ano atual

# ============================
# CONSTANTES GLOBAIS
# ============================
COR_PRIMARIA = "#3A6F1C"
COR_SECUNDARIA = "#7BBF4F"

# Custos
TAXA_FIXA_CARTAO = 2286.00
CUSTO_CARENCIA_FINANC = 1350.00
DEGRADACAO_PAINEL_ANUAL = 0.005

# Mapa de escalonamento do Fio B (Lei 14.300)
FIO_B_PERCENT_MAP = {
    2023: 15.0,
    2024: 30.0,
    2025: 45.0,
    2026: 60.0,
    2027: 75.0,
    2028: 90.0,
}

# Mapa do Custo de Disponibilidade (Res. 1000 ANEEL)
MINIMO_KWH_MAP = {
    "Monofásico": 30,
    "Bifásico": 50,
    "Trifásico": 100,
}

# ============================
# CSS CUSTOMIZADO
# ============================
CUSTOM_CSS = f"""
<style>
    /* Força o botão st.link_button(type="primary") a usar nossa cor primária */
    .st-emotion-cache-1v0mfe2.e10yg2x71 {{
        background-color: {COR_PRIMARIA};
        border-color: {COR_PRIMARIA};
    }}
    .st-emotion-cache-1v0mfe2.e10yg2x71:hover {{
        background-color: #2E5916;
        border-color: #2E5916;
    }}
    /* MUDANÇA (R8): Diminui fonte do Resumo da Projeção */
    .metric-container-markdown h4 {{
        font-size: 1.1rem;
        font-weight: 400;
        color: #555;
    }}
    .metric-container-markdown h3 {{
        font-size: 1.5rem;
        font-weight: 600;
        color: #000;
    }}
</style>
"""

# ============================
# FUNÇÃO DE FORMATAÇÃO DE MOEDA (R6)
# ============================
def format_currency_brl(valor: float) -> str:
    """
    Formata um float para o padrão de moeda brasileiro (R$ 1.234,56).
    """
    if valor is None:
        valor = 0.0
    # Formato padrão US: 1,234.56
    formatted_str = f"{valor:,.2f}"
    # Troca ',' por 'v' (temp) -> 1v234.56
    # Troca '.' por ',' -> 1v234,56
    # Troca 'v' por '.' -> 1.234,56
    formatted_str_brl = formatted_str.replace(",", "v").replace(".", ",").replace("v", ".")
    return f"R$ {formatted_str_brl}"

# ============================
# FUNÇÕES DE CALLBACK
# ============================
def sync_slider_1_from_2():
    """Se o slider 2 (único) mudar, atualiza o valor no state."""
    if "autoconsumo_slider_2" in st.session_state:
        st.session_state.autoconsumo_slider_1 = st.session_state.autoconsumo_slider_2


# ============================
# FUNÇÕES DE CÁLCULO
# ============================

def dimensionar_sistema(
    kwh_mensal: float,
    hsp: float,
    perdas_frac: float,
    pot_painel_w: int,
    qtd_paineis_override: int = None
) -> Tuple[int, float, float]:
    """Calcula o dimensionamento do sistema."""
    if kwh_mensal <= 0 or hsp <= 0 or pot_painel_w <= 0:
        return 0, 0.0, 0.0

    pot_painel_kw = pot_painel_w / 1000
    energia_por_painel_mes = pot_painel_kw * hsp * 30 * (1 - perdas_frac)

    if energia_por_painel_mes <= 0:
        return 0, 0.0, 0.0

    qtd_calc = kwh_mensal / energia_por_painel_mes
    qtd_recomendada = max(1, math.ceil(qtd_calc))

    if qtd_paineis_override is not None and qtd_paineis_override > 0:
        qtd = qtd_paineis_override
    else:
        qtd = qtd_recomendada

    kwp_total = qtd * pot_painel_kw
    geracao_mensal = qtd * energia_por_painel_mes
    return qtd, kwp_total, geracao_mensal


def calcular_fluxo_caixa(
    anos: int,
    valor_final_investimento: float,
    kwh_mensal_consumo: float,
    tarifa_kwh_inicial: float,
    geracao_mensal_inicial: float,
    autoconsumo_percent: int,
    fracao_fio_b_percent: float,
    ano_inicio_projeto: int,
    inflacao_energia: float,
    degradacao_anual: float,
    kwh_minimo_disponibilidade: int,
    taxa_iluminacao_publica: float # MUDANÇA (R2)
) -> pd.DataFrame:
    """
    Inclui o Custo de Disponibilidade e Iluminação Pública no fluxo.
    """
    lista_anos = list(range(1, anos + 1))
    
    fluxo_caixa_acumulado_list = []
    gasto_sem_solar_acumulado_list = []
    fluxo_fmt_list = []
    gasto_fmt_list = []
    
    fluxo_acumulado_com_solar = -valor_final_investimento
    gasto_acumulado_sem_solar = 0.0

    autoconsumo_frac = autoconsumo_percent / 100

    for ano in lista_anos:
        ano_calendario = ano_inicio_projeto + (ano - 1)
        percent_fio_b_a_pagar = FIO_B_PERCENT_MAP.get(ano_calendario, 100.0)
        
        # Inflação afeta a tarifa E a taxa de iluminação
        tarifa_kwh_inflacionada = tarifa_kwh_inicial * ((1 + inflacao_energia) ** (ano - 1))
        taxa_iluminacao_inflacionada = taxa_iluminacao_publica * ((1 + inflacao_energia) ** (ano - 1))
        
        geracao_mensal_degradada = geracao_mensal_inicial * ((1 - degradacao_anual) ** (ano - 1))

        # --- 1. Calcular Gasto SEM Solar (Linha Vermelha) ---
        gasto_anual_sem_solar = (kwh_mensal_consumo * 12 * tarifa_kwh_inflacionada) + (taxa_iluminacao_inflacionada * 12)
        gasto_acumulado_sem_solar += gasto_anual_sem_solar
        gasto_sem_solar_acumulado_list.append(gasto_acumulado_sem_solar)
        gasto_fmt_list.append(format_currency_brl(gasto_acumulado_sem_solar))

        # --- 2. Calcular Economia LÍQUIDA COM Solar (Linha Verde) ---
        gasto_antigo_anual = gasto_anual_sem_solar
        energia_excedente = geracao_mensal_degradada * (1 - autoconsumo_frac)
        
        # Custo Fio B
        valor_tarifa_fio_b_estimado_kwh = tarifa_kwh_inflacionada * (fracao_fio_b_percent / 100)
        custo_fio_b_anual = (
            energia_excedente * valor_tarifa_fio_b_estimado_kwh * (percent_fio_b_a_pagar / 100)
        ) * 12

        # Custo Disponibilidade
        custo_disponibilidade_anual = (kwh_minimo_disponibilidade * tarifa_kwh_inflacionada) * 12
        
        # MUDANÇA (R2): Taxa de iluminação é um custo fixo que permanece
        gasto_novo_anual = custo_fio_b_anual + custo_disponibilidade_anual + (taxa_iluminacao_inflacionada * 12)
        
        economia_liquida_anual = max(0, gasto_antigo_anual - gasto_novo_anual)

        fluxo_acumulado_com_solar += economia_liquida_anual
        fluxo_caixa_acumulado_list.append(fluxo_acumulado_com_solar)
        fluxo_fmt_list.append(format_currency_brl(fluxo_acumulado_com_solar))

    return pd.DataFrame({
        "Ano": lista_anos,
        "Gasto acumulado sem solar (R$)": gasto_sem_solar_acumulado_list,
        "Fluxo de caixa acumulado com solar (R$)": fluxo_caixa_acumulado_list,
        "Gasto Formatado": gasto_fmt_list,
        "Fluxo Formatado": fluxo_fmt_list,
    })


def gerar_pdf_proposta(dados: Dict[str, Any]) -> bytes:
    """MUDANÇA (R6): Corrigida formatação de moeda em todos os campos."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 2 * cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, y, "Proposta de Sistema Fotovoltaico – Brasil Enertech")
    y -= 1.2 * cm

    # --- Dados do Cliente ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Dados do Cliente")
    y -= 0.6 * cm
    c.setFont("Helvetica", 11)
    c.drawString(2 * cm, y, f"Nome: {dados['cliente']}")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Telefone: {dados['telefone']}")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Tipo de Conexão: {dados['tipo_conexao']}")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Consumo alvo: {dados['kwh_mensal']:.0f} kWh/mês")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Taxa de Iluminação Pública: {format_currency_brl(dados['taxa_iluminacao_publica'])}") # (R2)
    y -= 0.8 * cm

    # --- Resumo do Sistema ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Resumo do Sistema")
    y -= 0.6 * cm
    c.setFont("Helvetica", 11)
    c.drawString(2 * cm, y, f"Potência total: {dados['kwp_total']:.2f} kWp")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Quantidade de módulos: {dados['qtd_paineis']} x {dados['pot_painel_w']} W")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Geração mensal estimada: {dados['geracao_mensal']:.0f} kWh")
    y -= 0.8 * cm

    # --- Análise de Economia ---
    if 'custo_fio_b_mensal' in dados:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(2 * cm, y, "Análise de Economia (Estimativa Ano 1)")
        y -= 0.6 * cm
        c.setFont("Helvetica", 11)
        c.drawString(2 * cm, y, f"Autoconsumo considerado: {dados['autoconsumo_percent']:.0f}%")
        y -= 0.4 * cm
        c.drawString(2 * cm, y, f"Custo de Disponibilidade: {format_currency_brl(dados['custo_disponibilidade_mensal'])}")
        y -= 0.4 * cm
        c.drawString(2 * cm, y, f"Custo Fio B (Ano {dados['ano_inicio_projeto']}): {format_currency_brl(dados['custo_fio_b_mensal'])}")
        y -= 0.4 * cm
        # MUDANÇA (R6): Correção na formatação
        nova_fatura = dados['custo_disponibilidade_mensal'] + dados['custo_fio_b_mensal'] + dados['taxa_iluminacao_publica']
        c.drawString(2 * cm, y, f"Nova Fatura Estimada (Disp + Fio B + Ilum.): {format_currency_brl(nova_fatura)}")
        y -= 0.4 * cm
        c.drawString(2 * cm, y, f"Economia mensal líquida (Ano 1): {format_currency_brl(dados['economia_mensal'])}")
        y -= 0.4 * cm
        c.drawString(2 * cm, y, f"Economia acumulada em 25 anos: {format_currency_brl(dados['economia_25_anos'])}")
        y -= 0.8 * cm

    # --- Investimento ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Investimento e Condições Comerciais")
    y -= 0.6 * cm
    c.setFont("Helvetica", 11)
    # MUDANÇA (R6): Correção na formatação
    c.drawString(2 * cm, y, f"Valor base do sistema: {format_currency_brl(dados['valor_sistema_base'])}")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Modalidade de pagamento: {dados['modalidade']}")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Valor final da proposta: {format_currency_brl(dados['valor_final'])}")
    y -= 0.4 * cm
    if dados.get("parcela_mensal") is not None:
        c.drawString(2 * cm, y, f"Parcela mensal estimada: {format_currency_brl(dados['parcela_mensal'])}")
        y -= 0.4 * cm
    y -= 0.4 * cm
    
    c.showPage()
    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# ============================
# FUNÇÕES DE RENDERIZAÇÃO (UI)
# ============================

def renderizar_cabecalho():
    """MUDANÇA (R1): Adiciona link no Instagram e texto descritivo."""
    st.markdown(
        f"""
        <h1 style="text-align:center; color:{COR_PRIMARIA}; margin-bottom:0;">
            Brasil Enertech
        </h1>
        <h3 style="text-align:center; color:#444; margin-top:4px; margin-bottom:4px;">
            Gerador de Propostas
        </h3>
        <p style="text-align:center; color:#555; margin-bottom:10px;">
            Dimensione seu sistema de forma simples e prática.<br>
            Siga-nos no Instagram: <a href="https://www.instagram.com/brasilenertech" target="_blank"><b>@brasilenertech</b></a>
        </p>
        """,
        unsafe_allow_html=True
    )
    st.markdown("---")

def renderizar_entradas_cliente() -> Dict[str, Any]:
    """MUDANÇA (R1, R2, R7): Remove slider 1, adiciona Iluminação Pública, padrão Monofásico."""
    with st.container(border=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            nome_cliente = st.text_input("Nome do Cliente", "Cliente Exemplo")
            telefone = st.text_input("Telefone", "(82) 99999-9999")
        with col2:
            email = st.text_input("E-mail", "email@exemplo.com")
            kwh_mensal = st.number_input("Consumo alvo (kWh/mês)", min_value=50.0, step=10.0, value=800.0)
        with col3:
            tarifa_kwh = st.number_input("Tarifa de energia (R$/kWh)", min_value=0.5, max_value=3.0, value=1.10, step=0.05)

        col4, col5 = st.columns(2)
        with col4:
            # MUDANÇA (R7): Padrão index=0 (Monofásico)
            tipo_conexao = st.selectbox(
                "Tipo de Conexão",
                options=["Monofásico", "Bifásico", "Trifásico"],
                index=0, 
                help="Define o Custo de Disponibilidade (taxa mínima) - Monofásico: 30 kWh, Bifásico: 50 kWh, Trifásico: 100 kWh"
            )
        with col5:
            # MUDANÇA (R2): Adiciona campo de Iluminação Pública
            taxa_iluminacao_publica = st.number_input(
                "Taxa de Iluminação Pública (R$)",
                min_value=0.0, value=30.0, step=5.0,
                help="Valor da 'Cosip' ou 'CIP' que vem na sua conta. Este valor não muda com a energia solar."
            )
        
        # MUDANÇA (R1): Slider de autoconsumo desta seção foi REMOVIDO.

    return {
        "cliente": nome_cliente,
        "telefone": telefone,
        "email": email,
        "kwh_mensal": kwh_mensal,
        "tarifa_kwh": tarifa_kwh,
        "autoconsumo_percent": st.session_state.autoconsumo_slider_1, 
        "tipo_conexao": tipo_conexao,
        "taxa_iluminacao_publica": taxa_iluminacao_publica # Novo
    }

def renderizar_configuracoes_tecnicas() -> Dict[str, Any]:
    """MUDANÇA (R3): Ano de Conexão começa no ano atual."""
    with st.container(border=True):
        st.subheader("Configurações Técnicas e de Tarifa")
        
        st.markdown("##### Dados do Projeto")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            hsp = st.number_input("HSP (h/dia)", min_value=3.0, max_value=7.0, value=5.0, step=0.1)
        with col2:
            perdas_percent = st.number_input("Perdas globais (%)", min_value=5.0, max_value=25.0, value=15.0, step=1.0)
        with col3:
            pot_painel_w = st.selectbox("Potência do módulo (W)", [585, 605, 650, 700], index=2)
        with col4:
            valor_kwp = st.number_input("Valor do kWp (R$)", min_value=1500.0, max_value=4000.0, value=2200.0, step=50.0)
        
        st.markdown("##### Dados da Concessionária (Fio B)")
        col5, col6, col7 = st.columns(3)
        with col5:
             concessionaria = st.selectbox("Concessionária", ["Equatorial - AL", "Outra"])
        with col6:
            fracao_fio_b = st.number_input(
                "Fração Fio B da Tarifa (%)", 
                value=28.0, min_value=10.0, max_value=50.0, step=1.0,
                help="Estimativa da % da tarifa total que corresponde ao Fio B. (Média nacional: 28%)"
            )
        with col7:
            # MUDANÇA (R3): Pega o ano atual e o usa como padrão
            ano_atual = datetime.datetime.now().year
            anos_disponiveis = list(range(ano_atual, 2030)) # Lista de 2024 até 2029
            
            ano_inicio_projeto = st.selectbox(
                "Ano de Conexão (Lei 14.300)",
                options=anos_disponiveis,
                index=0, 
                help="Ano de entrada no sistema de compensação. Define a 'escada' de pagamento do Fio B."
            )

    return {
        "hsp": hsp,
        "perdas_percent": perdas_percent,
        "pot_painel_w": pot_painel_w,
        "valor_kwp": valor_kwp,
        "concessionaria": concessionaria,
        "fracao_fio_b_percent": fracao_fio_b,
        "ano_inicio_projeto": ano_inicio_projeto
    }

def renderizar_dimensionamento(
    kwh_mensal: float,
    hsp: float,
    perdas_percent: float,
    pot_painel_w: int,
    valor_kwp: float
) -> Dict[str, Any]:
    """Layout V5.1 mantido, pois é limpo."""
    
    perdas_frac = perdas_percent / 100
    
    qtd_recomendada, _, _ = dimensionar_sistema(kwh_mensal, hsp, perdas_frac, pot_painel_w)
    qtd_recomendada = qtd_recomendada or 1

    if "qtd_paineis" not in st.session_state:
        st.session_state.qtd_paineis = qtd_recomendada
        st.session_state.manual_override = False
    elif not st.session_state.manual_override:
        st.session_state.qtd_paineis = qtd_recomendada

    def diminuir_paineis():
        if st.session_state.qtd_paineis > 1:
            st.session_state.qtd_paineis -= 1
            st.session_state.manual_override = True

    def aumentar_paineis():
        st.session_state.qtd_paineis += 1
        st.session_state.manual_override = True

    def resetar_paineis():
        st.session_state.manual_override = False
        st.rerun() 

    with st.container(border=True):
        st.subheader("Dimensionamento do Sistema")
        
        col_btn_area, col_text_area = st.columns([1, 1])
        
        with col_btn_area:
            btn_cols = st.columns(3)
            with btn_cols[0]:
                st.button("➖", on_click=diminuir_paineis, use_container_width=True, help="Diminuir 1 painel")
            with btn_cols[1]:
                st.button("➕", on_click=aumentar_paineis, use_container_width=True, help="Aumentar 1 painel")
            with btn_cols[2]:
                st.button("Reset", on_click=resetar_paineis, use_container_width=True, help="Voltar à recomendação")

        with col_text_area:
            st.markdown(f"""
            <div style='margin-top: -8px;'> 
                **Qtd. atual: {st.session_state.qtd_paineis} painéis**
                <br>
                <small>(Recomendado: {qtd_recomendada})</small>
            </div>
            """, unsafe_allow_html=True)

        qtd_final, kwp_total, geracao_mensal = dimensionar_sistema(
            kwh_mensal, hsp, perdas_frac, pot_painel_w, st.session_state.qtd_paineis
        )
        valor_sistema_base = kwp_total * valor_kwp

        st.markdown("---")
        col_d1, col_d2, col_d3 = st.columns(3)
        with col_d1:
            st.metric("Potência total (kWp)", f"{kwp_total:.2f}")
        with col_d2:
            st.metric("Geração mensal (kWh)", f"{geracao_mensal:,.0f}")
        with col_d3:
            st.metric("Valor base do sistema", format_currency_brl(valor_sistema_base))
        
        st.caption(
            f"ℹ️ A geração mensal ({geracao_mensal:,.0f} kWh) pode ser maior que o consumo alvo ({kwh_mensal:,.0f} kWh) "
            "pois o sistema é arredondado para o número inteiro de painéis ( {qtd_final} ) necessário para atingir a meta."
        )
    
    return {
        "qtd_final": qtd_final,
        "kwp_total": kwp_total,
        "geracao_mensal": geracao_mensal,
        "valor_sistema_base": valor_sistema_base
    }

def renderizar_simulacao_economia(
    geracao_mensal: float,
    tarifa_kwh: float,
    fracao_fio_b_percent: float,
    ano_inicio_projeto: int,
    kwh_mensal_consumo: float,
    kwh_minimo_disponibilidade: int,
    taxa_iluminacao_publica: float
) -> Dict[str, Any]:
    """MUDANÇA (V7.1): Layout 2x2 para corrigir sobreposição de 4 colunas."""
    with st.container(border=True):
        st.subheader("Simulação de Economia (Estimativa Ano 1)")

        st.slider(
            "Autoconsumo instantâneo (%)", 
            min_value=10, max_value=100, 
            step=5,
            help="Mude aqui para ver o impacto no Fio B.",
            key="autoconsumo_slider_2",
            on_change=sync_slider_1_from_2
        )
        
        autoconsumo_percent = st.session_state.autoconsumo_slider_1
        autoconsumo_frac = autoconsumo_percent / 100

        # --- Cálculo da economia do ANO 1 ---
        percentual_pagamento_ano1 = FIO_B_PERCENT_MAP.get(ano_inicio_projeto, 100.0)
        gasto_antigo_mensal = (kwh_mensal_consumo * tarifa_kwh) + taxa_iluminacao_publica
        
        energia_excedente = geracao_mensal * (1 - autoconsumo_frac)
        valor_tarifa_fio_b_estimado_kwh = tarifa_kwh * (fracao_fio_b_percent / 100)
        custo_fio_b_mensal = (
            energia_excedente * valor_tarifa_fio_b_estimado_kwh * (percentual_pagamento_ano1 / 100)
        )
        custo_disponibilidade_mensal = kwh_minimo_disponibilidade * tarifa_kwh
        
        gasto_novo_mensal = custo_disponibilidade_mensal + custo_fio_b_mensal + taxa_iluminacao_publica
        
        economia_mensal_total = max(0, gasto_antigo_mensal - gasto_novo_mensal)
        economia_anual = economia_mensal_total * 12
        
        st.markdown("---")
        
        # MUDANÇA (V7.1): Layout novo (2 colunas principais)
        col1, col2 = st.columns(2)
        with col1:
            st.metric(
                label="✅ Economia Líquida (Mês)", 
                value=format_currency_brl(economia_mensal_total)
            )
        with col2:
            st.metric(
                label="🧾 Nova Fatura Estimada (Mês)", 
                value=format_currency_brl(gasto_novo_mensal),
                delta_color="off"
            )
        
        st.markdown("<br>", unsafe_allow_html=True) # Adiciona um espaço
        st.markdown("##### Detalhamento da Nova Fatura:")

        # MUDANÇA (V7.1): 3 colunas para o detalhamento (têm mais espaço)
        col3, col4, col5 = st.columns(3)
        with col3:
            st.metric(
                label="Custo Fio B", 
                value=format_currency_brl(custo_fio_b_mensal),
                delta_color="off"
            )
        with col4:
            st.metric(
                label="Custo Disponibilidade", 
                value=format_currency_brl(custo_disponibilidade_mensal),
                delta_color="off"
            )
        with col5:
            st.metric(
                label="Iluminação Pública", 
                value=format_currency_brl(taxa_iluminacao_publica),
                delta_color="off"
            )
            
        with st.expander("Ver detalhes do cálculo (Ano 1)"):
            st.markdown(f"**Gasto antigo (sem solar):** `{format_currency_brl(gasto_antigo_mensal)}`")
            st.markdown(f"**Gasto novo (com solar):** `{format_currency_brl(gasto_novo_mensal)}`")
            st.markdown("---")
            st.markdown(f"**Energia autoconsumida:** `{geracao_mensal * autoconsumo_frac:,.0f} kWh`")
            st.markdown(f"**Energia injetada (excedente):** `{energia_excedente:,.0f} kWh`")
            st.markdown(f"**Tarifa Fio B estimada:** `R$ {valor_tarifa_fio_b_estimado_kwh:,.4f}/kWh`")
            st.markdown(f"**Percentual Fio B pago ({ano_inicio_projeto}):** `{percentual_pagamento_ano1}%`")
            
    return {
        "economia_mensal": economia_mensal_total,
        "economia_anual": economia_anual,
        "custo_fio_b_mensal": custo_fio_b_mensal,
        "custo_disponibilidade_mensal": custo_disponibilidade_mensal,
        "autoconsumo_percent": autoconsumo_percent
    }

def renderizar_pagamento(
    valor_sistema_base: float,
    resultados_eco: Dict[str, Any]
) -> Dict[str, Any]:
    """MUDANÇA (R4, R5, R7, R13): Adiciona campos PF/PJ com formato e keys."""
    with st.container(border=True):
        st.subheader("Modalidades de Pagamento")

        modalidade = st.selectbox(
            "Escolha a forma de pagamento",
            ["À vista (5% desconto)", "Financiamento", "Cartão de crédito"]
        )

        valor_final = valor_sistema_base
        parcela_mensal = None
        dados_cliente_fin = {} # (R7)

        if modalidade.startswith("À vista"):
            valor_final = valor_sistema_base * 0.95
            st.success(f"**Valor Final:** {format_currency_brl(valor_final)}")

        elif modalidade == "Financiamento":
            st.info("ℹ️ Para agilizar sua análise de crédito, preencha os dados abaixo.")
            
            tipo_cliente = st.radio(
                "Tipo de Cliente", 
                ["Pessoa Física (PF)", "Pessoa Jurídica (PJ)"], 
                key="tipo_cliente_fin", 
                horizontal=True
            )
            
            if tipo_cliente == "Pessoa Física (PF)":
                col_pf1, col_pf2 = st.columns(2)
                with col_pf1:
                    # MUDANÇA (R5, R7): Placeholder e Key
                    cpf = st.text_input("CPF", placeholder="123.456.789-00", key="dado_financeiro_pf")
                with col_pf2:
                    # MUDANÇA (R4): Formato
                    data_nasc = st.date_input("Data de Nascimento", format="DD/MM/YYYY")
                endereco_pf = st.text_input("Endereço Completo (PF)", placeholder="Rua, Número, Bairro, CEP, Cidade - UF", key="endereco_pf")
                dados_cliente_fin = {"cpf": cpf, "data_nasc": data_nasc, "endereco": endereco_pf}
            
            else: # Pessoa Jurídica (PJ)
                col_pj1, col_pj2 = st.columns(2)
                with col_pj1:
                    # MUDANÇA (R5, R7): Placeholder e Key
                    cnpj = st.text_input("CNPJ", placeholder="12.345.678/0001-99", key="dado_financeiro_pj")
                with col_pj2:
                    # MUDANÇA (R4): Formato
                    data_abertura = st.date_input("Data de Abertura da Empresa", format="DD/MM/YYYY")
                endereco_pj = st.text_input("Endereço Completo (PJ)", placeholder="Rua, Número, Bairro, CEP, Cidade - UF", key="endereco_pj")
                ramo_pj = st.text_input("Atividade (Ramo)", placeholder="Ex: Comércio Varejista, Serviços, etc.", key="ramo_pj")
                dados_cliente_fin = {"cnpj": cnpj, "data_abertura": data_abertura, "endereco": endereco_pj, "ramo": ramo_pj}
                
            st.markdown("---")
            
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                meses = st.selectbox("Número de parcelas", [36, 48, 60, 72], index=1)
            with col_f2:
                carencia = st.selectbox("Carência (meses)", [0, 1, 2, 3], index=0)
            with col_f3:
                taxa_mes = st.slider("Taxa de juros a.m. (%)", min_value=1.2, max_value=2.8, value=1.5, step=0.1)

            valor_financiado = valor_sistema_base + (carencia * CUSTO_CARENCIA_FINANC)
            i = taxa_mes / 100
            
            if i > 0:
                parcela_mensal = (valor_financiado * i) / (1 - (1 + i) ** (-meses))
            else:
                parcela_mensal = valor_financiado / meses
            valor_final = parcela_mensal * meses

            st.info(f"Valor a ser financiado (com carência): {format_currency_brl(valor_financiado)}")
            st.success(f"**Parcela mensal:** {format_currency_brl(parcela_mensal)}")
            st.caption(f"Valor total ao final do financiamento: {format_currency_brl(valor_final)}")

        elif modalidade == "Cartão de crédito":
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                parcelas_cartao = st.selectbox("Número de parcelas", list(range(1, 22)), index=11)
            with col_c2:
                taxa_cartao = st.number_input("Taxa do cartão a.m. (%)", min_value=0.5, max_value=3.0, value=1.25, step=0.05)

            valor_com_taxa_fixa = valor_sistema_base + TAXA_FIXA_CARTAO
            i = taxa_cartao / 100
            
            if i > 0:
                parcela_mensal = (valor_com_taxa_fixa * i) / (1 - (1 + i) ** (-parcelas_cartao))
            else:
                parcela_mensal = valor_com_taxa_fixa / parcelas_cartao
            valor_final = parcela_mensal * parcelas_cartao

            st.info(f"Valor base com taxa fixa: {format_currency_brl(valor_com_taxa_fixa)}")
            st.success(f"**Parcela aproximada:** {format_currency_brl(parcela_mensal)}")
            st.caption(f"Valor total no cartão: {format_currency_brl(valor_final)}")
        
        
        # MUDANÇA (R13): Tabela/Balanço Comparativo
        if parcela_mensal is not None:
            st.markdown("---")
            st.subheader("Balanço Financeiro (Estimativa Ano 1)")
            
            economia_liquida = resultados_eco.get('economia_mensal', 0)
            resultado_final = economia_liquida - parcela_mensal
            
            col_bal1, col_bal2, col_bal3 = st.columns(3)
            with col_bal1:
                st.metric("Sua Economia Mensal", format_currency_brl(economia_liquida))
            with col_bal2:
                st.metric("Sua Parcela Mensal", format_currency_brl(parcela_mensal))
            with col_bal3:
                st.metric("Resultado (Economia - Parcela)", 
                           format_currency_brl(resultado_final),
                           delta_color="normal" if resultado_final > 0 else "inverse")
            
            if resultado_final > 0:
                st.success(f"O sistema se 'paga' desde a primeira parcela, gerando uma folga de {format_currency_brl(resultado_final)} por mês.")
            else:
                st.warning(f"Sua parcela será {format_currency_brl(abs(resultado_final))} maior que sua economia inicial. A inflação energética deve compensar isso nos próximos anos.")

    return {
        "modalidade": modalidade,
        "valor_final": valor_final,
        "parcela_mensal": parcela_mensal,
        "dados_cliente_fin": dados_cliente_fin # (R7)
    }

def renderizar_projecao_financeira(
    valor_final_investimento: float,
    kwh_mensal_consumo: float,
    tarifa_kwh_inicial: float,
    geracao_mensal_inicial: float,
    autoconsumo_percent: int,
    fracao_fio_b_percent: float,
    ano_inicio_projeto: int,
    kwh_minimo_disponibilidade: int,
    taxa_iluminacao_publica: float # MUDANÇA (R2)
):
    """MUDANÇA (R8, R10, R11): Payback em meses, tooltips R$ e fontes menores."""
    with st.container(border=True):
        st.subheader("Projeção Financeira (Payback) – 25 anos")
        
        inflacao_energia = st.slider(
            "Inflação média da energia a.a. (%)",
            min_value=3.0, max_value=10.0, value=5.0, step=0.5
        ) / 100

        df_plot = calcular_fluxo_caixa(
            anos=25,
            valor_final_investimento=valor_final_investimento,
            kwh_mensal_consumo=kwh_mensal_consumo,
            tarifa_kwh_inicial=tarifa_kwh_inicial,
            geracao_mensal_inicial=geracao_mensal_inicial,
            autoconsumo_percent=autoconsumo_percent,
            fracao_fio_b_percent=fracao_fio_b_percent,
            ano_inicio_projeto=ano_inicio_projeto,
            inflacao_energia=inflacao_energia,
            degradacao_anual=DEGRADACAO_PAINEL_ANUAL,
            kwh_minimo_disponibilidade=kwh_minimo_disponibilidade,
            taxa_iluminacao_publica=taxa_iluminacao_publica # (R2)
        )

        fig = go.Figure()
        
        # Linha 1: Gasto sem solar
        fig.add_trace(go.Scatter(
            x=df_plot["Ano"],
            y=df_plot["Gasto acumulado sem solar (R$)"],
            customdata=df_plot["Gasto Formatado"], # (R11)
            mode="lines",
            name="Gasto Acumulado SEM Solar",
            line=dict(color="#D9534F", width=2, dash="dot"),
            fill='tozeroy',
            hovertemplate="<b>Ano %{x}</b><br>Gasto Acumulado: <b>%{customdata}</b><extra></extra>"
        ))
        
        # Linha 2: Economia com solar
        fig.add_trace(go.Scatter(
            x=df_plot["Ano"],
            y=df_plot["Fluxo de caixa acumulado com solar (R$)"],
            customdata=df_plot["Fluxo Formatado"], # (R11)
            mode="lines",
            name="Economia Acumulada COM Solar",
            line=dict(color=COR_PRIMARIA, width=4),
            fill='tozeroy',
            hovertemplate="<b>Ano %{x}</b><br>Economia Acumulada: <b>%{customdata}</b><extra></extra>"
        ))
        
        fig.add_hline(y=0, line_width=2, line_dash="dash", line_color="black")
        
        # MUDANÇA (R10): Cálculo do Payback em Meses
        payback_ano_str = "+ 25 anos"
        try:
            anos_positivos_df = df_plot[df_plot["Fluxo de caixa acumulado com solar (R$)"] > 0]
            if not anos_positivos_df.empty:
                payback_ano_num = anos_positivos_df["Ano"].iloc[0]
                
                if payback_ano_num == 1:
                    valor_inicial = -valor_final_investimento
                    valor_final_ano_1 = anos_positivos_df["Fluxo de caixa acumulado com solar (R$)"].iloc[0]
                    ganho_no_ano = valor_final_ano_1 - valor_inicial
                    
                    if ganho_no_ano > 0:
                        fracao_ano = abs(valor_inicial) / ganho_no_ano
                        total_meses = max(1, int(round(fracao_ano * 12)))
                        payback_ano_str = f"~ {total_meses} meses"
                    else:
                        payback_ano_str = "Ano 1"
                else:
                    valor_final_ano_anterior = df_plot[df_plot["Ano"] == payback_ano_num - 1]["Fluxo de caixa acumulado com solar (R$)"].iloc[0]
                    valor_final_ano_atual = anos_positivos_df["Fluxo de caixa acumulado com solar (R$)"].iloc[0]
                    ganho_no_ano = valor_final_ano_atual - valor_final_ano_anterior
                    
                    if ganho_no_ano > 0:
                        fracao_ano = abs(valor_final_ano_anterior) / ganho_no_ano
                        meses_adicionais = int(round(fracao_ano * 12))
                        total_meses = int((payback_ano_num - 1) * 12 + meses_adicionais)
                        payback_ano_str = f"~ {total_meses} meses"
                    else:
                        payback_ano_str = f"Ano {payback_ano_num}"
        except Exception:
             pass

        fig.update_layout(
            title="Projeção: Gasto Acumulado vs. Economia Acumulada",
            xaxis_title="Ano",
            yaxis_title="Reais (R$)",
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)
        
        economia_25_anos = df_plot["Fluxo de caixa acumulado com solar (R$)"].iloc[-1]
        
        # MUDANÇA (R8): Usa Markdown para fontes menores
        st.markdown("---")
        st.markdown("<h4 style='text-align: center; margin-bottom: 0px;'>Resumo da Projeção</h4>", unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"""
            <div class="metric-container-markdown" style="text-align: center;">
                <h4>Investimento Inicial</h4>
                <h3>{format_currency_brl(valor_final_investimento)}</h3>
            </div>
            """, unsafe_allow_html=True)
        with col2:
            st.markdown(f"""
            <div class="metric-container-markdown" style="text-align: center;">
                <h4>Payback Estimado</h4>
                <h3>{payback_ano_str}</h3>
            </div>
            """, unsafe_allow_html=True)
        with col3:
            st.markdown(f"""
            <div class="metric-container-markdown" style="text-align: center;">
                <h4>Economia em 25 Anos</h4>
                <h3>{format_currency_brl(economia_25_anos)}</h3>
            </div>
            """, unsafe_allow_html=True)

        return {
            "economia_25_anos": economia_25_anos,
            "payback_str": payback_ano_str # Passa o payback
        }

def renderizar_exportar_proposta(dados_pdf: Dict[str, Any]):
    """MUDANÇA (R7, R12): Validação de dados e melhor CTA."""
    with st.container(border=True):
        st.subheader("Finalizar Proposta")

        # MUDANÇA (R7): Validação
        dados_completos = True
        modalidade = dados_pdf.get('modalidade')
        
        if modalidade == "Financiamento":
            tipo_cliente = st.session_state.get("tipo_cliente_fin", "Pessoa Física (PF)")
            if tipo_cliente == "Pessoa Física (PF)":
                if not st.session_state.get("dado_financeiro_pf") or not st.session_state.get("endereco_pf"):
                    dados_completos = False
                    msg_erro = "Preencha os campos de CPF e Endereço na seção 'Modalidades de Pagamento' para prosseguir."
            else: # PJ
                if not st.session_state.get("dado_financeiro_pj") or not st.session_state.get("endereco_pj"):
                    dados_completos = False
                    msg_erro = "Preencha os campos de CNPJ e Endereço na seção 'Modalidades de Pagamento' para prosseguir."
        
        if not dados_completos:
            st.warning(f"⚠️ {msg_erro}")
            st.button("✅ Solicitar Visita Técnica pelo WhatsApp!", disabled=True, use_container_width=True)
        else:
            # Dados estão completos, renderiza o botão real
            nova_fatura_minima = dados_pdf['custo_disponibilidade_mensal'] + dados_pdf['custo_fio_b_mensal'] + dados_pdf['taxa_iluminacao_publica']
            
            # Adiciona dados financeiros ao texto do WhatsApp
            dados_fin_str = ""
            if modalidade == "Financiamento":
                dados_fin = dados_pdf['dados_cliente_fin']
                if "cpf" in dados_fin:
                    dados_fin_str = f"\n--- DADOS P/ ANÁLISE (PF) ---\nCPF: {dados_fin['cpf']}\nNasc: {dados_fin['data_nasc']}\nEnd: {dados_fin['endereco']}"
                elif "cnpj" in dados_fin:
                    dados_fin_str = f"\n--- DADOS P/ ANÁLISE (PJ) ---\nCNPJ: {dados_fin['cnpj']}\nAbertura: {dados_fin['data_abertura']}\nEnd: {dados_fin['endereco']}\nRamo: {dados_fin['ramo']}"

            
            texto_whats = (
                f"Olá! Usei o simulador Brasil Enertech e gostaria de validar minha proposta.\n\n"
                f"--- RESUMO DA SIMULAÇÃO (ANO 1) ---\n"
                f"Cliente: {dados_pdf['cliente']}\n"
                f"Telefone: {dados_pdf['telefone']}\n"
                f"Conexão: {dados_pdf['tipo_conexao']}\n"
                f"Consumo: {dados_pdf['kwh_mensal']:.0f} kWh\n"
                f"Autoconsumo: {dados_pdf['autoconsumo_percent']:.0f}%\n"
                f"Sistema: {dados_pdf['kwp_total']:.2f} kWp ({dados_pdf['qtd_paineis']} x {dados_pdf['pot_painel_w']} W)\n"
                f"Valor Final: {format_currency_brl(dados_pdf['valor_final'])} ({dados_pdf['modalidade']})\n"
                f"--- FINANCEIRO (MÊS) ---\n"
                f"Economia Líquida: {format_currency_brl(dados_pdf['economia_mensal'])}\n"
                f"Nova Fatura Estimada: {format_currency_brl(nova_fatura_minima)}\n"
                f"Payback: {dados_pdf['payback_str']}\n"
                f"{dados_fin_str}\n\n"
                "Podemos agendar uma visita técnica?"
            )
            link_whats = f"https://wa.me/5582998098501?text={quote(texto_whats)}"
            
            # MUDANÇA (R12): Chamada para ação
            st.link_button(
                "✅ Solicitar Visita Técnica pelo WhatsApp!",
                link_whats,
                type="primary", 
                use_container_width=True
            )


# ============================
# EXECUÇÃO PRINCIPAL (MAIN)
# ============================
def main():
    st.set_page_config(
        page_title="Brasil Enertech – Gerador de Propostas",
        layout="centered",
        page_icon="☀️"
    )
    
    # Inicializa o state do slider
    if "autoconsumo_slider_1" not in st.session_state:
        st.session_state.autoconsumo_slider_1 = 40 # Valor padrão
    if "autoconsumo_slider_2" not in st.session_state:
        st.session_state.autoconsumo_slider_2 = st.session_state.autoconsumo_slider_1
    
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    renderizar_cabecalho()

    # 1. Coletar dados do cliente
    inputs_cliente = renderizar_entradas_cliente()
    kwh_minimo_disponibilidade = MINIMO_KWH_MAP.get(inputs_cliente["tipo_conexao"], 100)
    taxa_iluminacao_publica = inputs_cliente["taxa_iluminacao_publica"] # (R2)

    # 2. Coletar dados técnicos
    inputs_tecnicos = renderizar_configuracoes_tecnicas()

    # 3. Calcular dimensionamento
    resultados_dim = renderizar_dimensionamento(
        kwh_mensal=inputs_cliente["kwh_mensal"],
        hsp=inputs_tecnicos["hsp"],
        perdas_percent=inputs_tecnicos["perdas_percent"],
        pot_painel_w=inputs_tecnicos["pot_painel_w"],
        valor_kwp=inputs_tecnicos["valor_kwp"]
    )

    # 4. Calcular simulação de economia (ANO 1)
    resultados_eco = renderizar_simulacao_economia(
        geracao_mensal=resultados_dim["geracao_mensal"],
        tarifa_kwh=inputs_cliente["tarifa_kwh"],
        fracao_fio_b_percent=inputs_tecnicos["fracao_fio_b_percent"],
        ano_inicio_projeto=inputs_tecnicos["ano_inicio_projeto"],
        kwh_mensal_consumo=inputs_cliente["kwh_mensal"],
        kwh_minimo_disponibilidade=kwh_minimo_disponibilidade,
        taxa_iluminacao_publica=taxa_iluminacao_publica # (R2)
    )
    autoconsumo_atualizado = resultados_eco["autoconsumo_percent"]

    # 5. Calcular pagamento
    resultados_pag = renderizar_pagamento(
        valor_sistema_base=resultados_dim["valor_sistema_base"],
        resultados_eco=resultados_eco
    )

    # 6. Calcular projeção financeira (25 anos)
    resultados_proj = renderizar_projecao_financeira(
        valor_final_investimento=resultados_pag["valor_final"],
        kwh_mensal_consumo=inputs_cliente["kwh_mensal"],
        tarifa_kwh_inicial=inputs_cliente["tarifa_kwh"],
        geracao_mensal_inicial=resultados_dim["geracao_mensal"],
        autoconsumo_percent=autoconsumo_atualizado,
        fracao_fio_b_percent=inputs_tecnicos["fracao_fio_b_percent"],
        ano_inicio_projeto=inputs_tecnicos["ano_inicio_projeto"],
        kwh_minimo_disponibilidade=kwh_minimo_disponibilidade,
        taxa_iluminacao_publica=taxa_iluminacao_publica # (R2)
    )

    # 7. Preparar dados e renderizar exportação
    dados_pdf_final = {
        **inputs_cliente,
        "autoconsumo_percent": autoconsumo_atualizado,
        **inputs_tecnicos,
        **resultados_dim,
        **resultados_eco,
        **resultados_pag,
        **resultados_proj,
        "qtd_paineis": resultados_dim["qtd_final"]
    }

    renderizar_exportar_proposta(dados_pdf_final)
    
    # MUDANÇA (R5): PDF Backup REMOVIDO

if __name__ == "__main__":
    main()