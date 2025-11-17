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

# ============================
# CONSTANTES GLOBAIS
# ============================
COR_PRIMARIA = "#3A6F1C"
COR_SECUNDARIA = "#7BBF4F"

# Custos
TAXA_FIXA_CARTAO = 2286.00
CUSTO_CARENCIA_FINANC = 1350.00
DEGRADACAO_PAINEL_ANUAL = 0.005 # 0.5% de perda de eficiência ao ano

# Mapa de escalonamento do Fio B (Lei 14.300)
FIO_B_PERCENT_MAP = {
    2023: 15.0,
    2024: 30.0,
    2025: 45.0,
    2026: 60.0,
    2027: 75.0,
    2028: 90.0,
    # De 2029 em diante, é 100%
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
        background-color: #2E5916; /* Um tom de verde mais escuro no hover */
        border-color: #2E5916;
    }}
</style>
"""

# ============================
# FUNÇÕES DE CALLBACK (Sincronia dos sliders)
# ============================
def sync_slider_2_from_1():
    """Se o slider 1 mudar, atualiza o valor do slider 2."""
    if "autoconsumo_slider_1" in st.session_state:
        st.session_state.autoconsumo_slider_2 = st.session_state.autoconsumo_slider_1

def sync_slider_1_from_2():
    """Se o slider 2 mudar, atualiza o valor do slider 1."""
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
    kwh_minimo_disponibilidade: int
) -> pd.DataFrame:
    """
    Inclui o Custo de Disponibilidade no cálculo do fluxo de caixa.
    """
    lista_anos = list(range(1, anos + 1))
    
    fluxo_caixa_acumulado_list = []
    gasto_sem_solar_acumulado_list = []
    
    fluxo_acumulado_com_solar = -valor_final_investimento
    gasto_acumulado_sem_solar = 0.0

    autoconsumo_frac = autoconsumo_percent / 100

    for ano in lista_anos:
        ano_calendario = ano_inicio_projeto + (ano - 1)
        percent_fio_b_a_pagar = FIO_B_PERCENT_MAP.get(ano_calendario, 100.0)
        
        tarifa_kwh_inflacionada = tarifa_kwh_inicial * ((1 + inflacao_energia) ** (ano - 1))
        geracao_mensal_degradada = geracao_mensal_inicial * ((1 - degradacao_anual) ** (ano - 1))

        # --- 1. Calcular Gasto SEM Solar (Linha Vermelha) ---
        gasto_anual_sem_solar = (kwh_mensal_consumo * 12 * tarifa_kwh_inflacionada)
        gasto_acumulado_sem_solar += gasto_anual_sem_solar
        gasto_sem_solar_acumulado_list.append(gasto_acumulado_sem_solar)

        # --- 2. Calcular Economia LÍQUIDA COM Solar (Linha Verde) ---
        gasto_antigo_anual = gasto_anual_sem_solar
        energia_excedente = geracao_mensal_degradada * (1 - autoconsumo_frac)
        
        # Custo do Fio B (pago sobre a energia EXCEDENTE)
        valor_tarifa_fio_b_estimado_kwh = tarifa_kwh_inflacionada * (fracao_fio_b_percent / 100)
        custo_fio_b_anual = (
            energia_excedente * valor_tarifa_fio_b_estimado_kwh * (percent_fio_b_a_pagar / 100)
        ) * 12

        # Custo de Disponibilidade (Taxa Mínima)
        custo_disponibilidade_anual = (kwh_minimo_disponibilidade * tarifa_kwh_inflacionada) * 12

        gasto_novo_anual = custo_fio_b_anual + custo_disponibilidade_anual
        economia_liquida_anual = max(0, gasto_antigo_anual - gasto_novo_anual)

        fluxo_acumulado_com_solar += economia_liquida_anual
        fluxo_caixa_acumulado_list.append(fluxo_acumulado_com_solar)

    return pd.DataFrame({
        "Ano": lista_anos,
        "Gasto acumulado sem solar (R$)": gasto_sem_solar_acumulado_list,
        "Fluxo de caixa acumulado com solar (R$)": fluxo_caixa_acumulado_list
    })


def gerar_pdf_proposta(dados: Dict[str, Any]) -> bytes:
    """Adiciona Tipo de Conexão e Custo Disponibilidade ao PDF."""
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
        c.drawString(2 * cm, y, f"Custo de Disponibilidade: R$ {dados['custo_disponibilidade_mensal']:,.2f}")
        y -= 0.4 * cm
        c.drawString(2 * cm, y, f"Custo Fio B (Ano {dados['ano_inicio_projeto']}): R$ {dados['custo_fio_b_mensal']:,.2f}")
        y -= 0.4 * cm
        c.drawString(2 * cm, y, f"Nova Fatura Mínima Estimada: R$ {dados['custo_disponibilidade_mensal'] + dados['custo_fio_b_mensal']:,.2f}")
        y -= 0.4 * cm
        c.drawString(2 * cm, y, f"Economia mensal líquida (Ano 1): R$ {dados['economia_mensal']:,.2f}")
        y -= 0.4 * cm
        c.drawString(2 * cm, y, f"Economia acumulada em 25 anos: R$ {dados['economia_25_anos']:,.2f}")
        y -= 0.8 * cm

    # --- Investimento ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Investimento e Condições Comerciais")
    y -= 0.6 * cm
    c.setFont("Helvetica", 11)
    c.drawString(2 * cm, y, f"Valor base do sistema: R$ {dados['valor_sistema_base']:,.2f}")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Modalidade de pagamento: {dados['modalidade']}")
    y -= 0.4 * cm
    c.drawString(2 * cm, y, f"Valor final da proposta: R$ {dados['valor_final']:,.2f}")
    y -= 0.4 * cm
    if dados.get("parcela_mensal") is not None:
        c.drawString(2 * cm, y, f"Parcela mensal estimada: R$ {dados['parcela_mensal']:,.2f}")
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
    """Renderiza o cabeçalho da página."""
    st.markdown(
        f"""
        <h1 style="text-align:center; color:{COR_PRIMARIA}; margin-bottom:0;">
            Brasil Enertech
        </h1>
        <h3 style="text-align:center; color:#444; margin-top:4px;">
            Gerador de Propostas
        </h3>
        """,
        unsafe_allow_html=True
    )
    st.markdown("---")

def renderizar_entradas_cliente() -> Dict[str, Any]:
    """Adiciona 'Tipo de Conexão'."""
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
            # Novo campo 'Tipo de Conexão'
            tipo_conexao = st.selectbox(
                "Tipo de Conexão",
                options=["Monofásico", "Bifásico", "Trifásico"],
                index=2, # Padrão Trifásico (100 kWh)
                help="Define o Custo de Disponibilidade (taxa mínima) - Monofásico: 30 kWh, Bifásico: 50 kWh, Trifásico: 100 kWh"
            )
        with col5:
            # Slider 1
            st.slider(
                "Autoconsumo instantâneo (%)", 
                min_value=10, max_value=100, 
                step=5,
                help="Qual % da energia gerada é consumida NA HORA? O restante é injetado e pagará o Fio B.",
                key="autoconsumo_slider_1",
                on_change=sync_slider_2_from_1
            )

    return {
        "cliente": nome_cliente,
        "telefone": telefone,
        "email": email,
        "kwh_mensal": kwh_mensal,
        "tarifa_kwh": tarifa_kwh,
        "autoconsumo_percent": st.session_state.autoconsumo_slider_1,
        "tipo_conexao": tipo_conexao
    }

def renderizar_configuracoes_tecnicas() -> Dict[str, Any]:
    """Renderiza os inputs de configurações técnicas."""
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
            ano_inicio_projeto = st.selectbox(
                "Ano de Conexão (Lei 14.300)",
                options=[2024, 2025, 2026, 2027, 2028, 2029],
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
    """CORREÇÃO (V5.1): Layout dos botões de ajuste com colunas aninhadas."""
    
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
        
        # Colunas principais (Botões | Texto)
        col_btn_area, col_text_area = st.columns([1, 1])
        
        with col_btn_area:
            # Colunas aninhadas para os botões
            btn_cols = st.columns(3)
            with btn_cols[0]:
                st.button("➖", on_click=diminuir_paineis, use_container_width=True, help="Diminuir 1 painel")
            with btn_cols[1]:
                st.button("➕", on_click=aumentar_paineis, use_container_width=True, help="Aumentar 1 painel")
            with btn_cols[2]:
                st.button("Reset", on_click=resetar_paineis, use_container_width=True, help="Voltar à recomendação")

        with col_text_area:
            # O 'style' diminui o espaço superior do texto para alinhar melhor
            st.markdown(f"""
            <div style='margin-top: -8px;'> 
                **Qtd. atual: {st.session_state.qtd_paineis} painéis**
                <br>
                <small>(Recomendado: {qtd_recomendada})</small>
            </div>
            """, unsafe_allow_html=True)

        # Recalcula tudo com a quantidade final
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
            st.metric("Valor base do sistema", f"R$ {valor_sistema_base:,.2f}")
        
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
    kwh_minimo_disponibilidade: int
) -> Dict[str, Any]:
    """CORREÇÃO (V5.1): Revertendo para st.metric para corrigir layout e sobreposição."""
    with st.container(border=True):
        st.subheader("Simulação de Economia (Estimativa Ano 1)")

        # Slider 2 (sincronizado)
        st.slider(
            "Autoconsumo instantâneo (%)", 
            min_value=10, max_value=100, 
            step=5,
            help="Mude aqui para ver o impacto no Fio B. Está sincronizado com o slider do topo.",
            key="autoconsumo_slider_2",
            on_change=sync_slider_1_from_2
        )
        
        autoconsumo_percent = st.session_state.autoconsumo_slider_1
        autoconsumo_frac = autoconsumo_percent / 100

        # --- Cálculo da economia do ANO 1 ---
        percentual_pagamento_ano1 = FIO_B_PERCENT_MAP.get(ano_inicio_projeto, 100.0)
        gasto_antigo_mensal = kwh_mensal_consumo * tarifa_kwh
        energia_excedente = geracao_mensal * (1 - autoconsumo_frac)
        valor_tarifa_fio_b_estimado_kwh = tarifa_kwh * (fracao_fio_b_percent / 100)
        custo_fio_b_mensal = (
            energia_excedente * valor_tarifa_fio_b_estimado_kwh * (percentual_pagamento_ano1 / 100)
        )
        custo_disponibilidade_mensal = kwh_minimo_disponibilidade * tarifa_kwh
        gasto_novo_mensal = custo_disponibilidade_mensal + custo_fio_b_mensal
        economia_mensal_total = max(0, gasto_antigo_mensal - gasto_novo_mensal)
        economia_anual = economia_mensal_total * 12
        
        st.markdown("---")
        
        # Voltamos ao st.metric. É mais limpo e responsivo.
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(
                label="Economia Líquida (Mês)", 
                value=f"R$ {economia_mensal_total:,.2f}"
            )
        with col2:
            st.metric(
                label="Custo Fio B (Mês)", 
                value=f"R$ {custo_fio_b_mensal:,.2f}",
                delta_color="off" # Neutro
            )
        with col3:
            st.metric(
                label="Custo Disponibilidade", 
                value=f"R$ {custo_disponibilidade_mensal:,.2f}",
                delta_color="off" # Neutro
            )
            
        st.info(f"Sua nova fatura mínima estimada (Ano 1) será de **R$ {gasto_novo_mensal:,.2f} /mês** (Custo Fio B + Disponibilidade).")

        with st.expander("Ver detalhes do cálculo (Ano 1)"):
            st.markdown(f"**Gasto antigo (sem solar):** `R$ {gasto_antigo_mensal:,.2f}`")
            st.markdown(f"**Gasto novo (com solar):** `R$ {gasto_novo_mensal:,.2f}`")
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

def renderizar_pagamento(valor_sistema_base: float) -> Dict[str, Any]:
    """Renderiza as modalidades de pagamento."""
    with st.container(border=True):
        st.subheader("Modalidades de Pagamento")

        modalidade = st.selectbox(
            "Escolha a forma de pagamento",
            ["À vista (5% desconto)", "Financiamento", "Cartão de crédito"]
        )

        valor_final = valor_sistema_base
        parcela_mensal = None

        if modalidade.startswith("À vista"):
            valor_final = valor_sistema_base * 0.95
            st.success(f"**Valor Final:** R$ {valor_final:,.2f}")

        elif modalidade == "Financiamento":
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

            st.info(f"Valor a ser financiado (com carência): R$ {valor_financiado:,.2f}")
            st.success(f"**Parcela mensal:** R$ {parcela_mensal:,.2f}")
            st.caption(f"Valor total ao final do financiamento: R$ {valor_final:,.2f}")

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

            st.info(f"Valor base com taxa fixa: R$ {valor_com_taxa_fixa:,.2f}")
            st.success(f"**Parcela aproximada:** R$ {parcela_mensal:,.2f}")
            st.caption(f"Valor total no cartão: R$ {valor_final:,.2f}")

    return {
        "modalidade": modalidade,
        "valor_final": valor_final,
        "parcela_mensal": parcela_mensal
    }

def renderizar_projecao_financeira(
    valor_final_investimento: float,
    kwh_mensal_consumo: float,
    tarifa_kwh_inicial: float,
    geracao_mensal_inicial: float,
    autoconsumo_percent: int,
    fracao_fio_b_percent: float,
    ano_inicio_projeto: int,
    kwh_minimo_disponibilidade: int
):
    """Adiciona métricas de resumo (KPIs) abaixo do gráfico."""
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
            kwh_minimo_disponibilidade=kwh_minimo_disponibilidade
        )

        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=df_plot["Ano"],
            y=df_plot["Gasto acumulado sem solar (R$)"],
            mode="lines",
            name="Gasto Acumulado SEM Solar",
            line=dict(color="#D9534F", width=2, dash="dot"),
            fill='tozeroy'
        ))
        
        fig.add_trace(go.Scatter(
            x=df_plot["Ano"],
            y=df_plot["Fluxo de caixa acumulado com solar (R$)"],
            mode="lines",
            name="Economia Acumulada COM Solar",
            line=dict(color=COR_PRIMARIA, width=4),
            fill='tozeroy'
        ))
        
        fig.add_hline(y=0, line_width=2, line_dash="dash", line_color="black")
        
        payback_ano_str = "N/A"
        try:
            payback_ano_num = df_plot[df_plot["Fluxo de caixa acumulado com solar (R$)"] > 0]["Ano"].iloc[0]
            payback_ano_str = f"Ano {payback_ano_num}"
        except IndexError:
            payback_ano_str = "+ 25 anos"

        fig.update_layout(
            title="Projeção: Gasto Acumulado vs. Economia Acumulada",
            xaxis_title="Ano",
            yaxis_title="R$",
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)
        
        economia_25_anos = df_plot["Fluxo de caixa acumulado com solar (R$)"].iloc[-1]
        
        st.markdown("---")
        st.markdown("<h4 style='text-align: center;'>Resumo da Projeção</h4>", unsafe_allow_html=True)
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Investimento Inicial", f"R$ {valor_final_investimento:,.2f}")
        with col2:
            st.metric("Payback Estimado", payback_ano_str)
        with col3:
            st.metric("Economia Líquida em 25 Anos", f"R$ {economia_25_anos:,.2f}")

        return {
            "economia_25_anos": economia_25_anos
        }

def renderizar_exportar_proposta(dados_pdf: Dict[str, Any]):
    """Adiciona os novos custos ao texto do WhatsApp."""
    with st.container(border=True):
        st.subheader("Finalizar Proposta")

        nova_fatura_minima = dados_pdf['custo_disponibilidade_mensal'] + dados_pdf['custo_fio_b_mensal']
        
        texto_whats = (
            f"Olá! Gostaria de um orçamento da Brasil Enertech.\n\n"
            f"Simulei uma proposta no site para o cliente: {dados_pdf['cliente']}\n"
            f"--- DADOS DA SIMULAÇÃO (ANO 1) ---\n"
            f"- Tipo de Conexão: {dados_pdf['tipo_conexao']}\n"
            f"- Consumo Alvo: {dados_pdf['kwh_mensal']:.0f} kWh\n"
            f"- Autoconsumo: {dados_pdf['autoconsumo_percent']:.0f}%\n"
            f"- Potência do sistema: {dados_pdf['kwp_total']:.2f} kWp\n"
            f"- Geração mensal: {dados_pdf['geracao_mensal']:,.0f} kWh\n"
            f"- Valor final: R$ {dados_pdf['valor_final']:,.2f}\n"
            f"- Modalidade: {dados_pdf['modalidade']}\n"
            f"- Economia líquida mensal: R$ {dados_pdf['economia_mensal']:,.2f}\n"
            f"- Nova Fatura Mínima (Disp. + Fio B): R$ {nova_fatura_minima:,.2f}\n\n"
            "Podemos validar esta proposta?"
        )
        link_whats = f"https://wa.me/5582998098501?text={quote(texto_whats)}"
        
        st.link_button(
            "📲 Receber Proposta Detalhada no WhatsApp!",
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
    
    # Inicializa os keys dos sliders
    if "autoconsumo_slider_1" not in st.session_state:
        st.session_state.autoconsumo_slider_1 = 40 # Valor padrão
    if "autoconsumo_slider_2" not in st.session_state:
        st.session_state.autoconsumo_slider_2 = st.session_state.autoconsumo_slider_1
    
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    renderizar_cabecalho()

    # 1. Coletar dados do cliente
    inputs_cliente = renderizar_entradas_cliente()
    kwh_minimo_disponibilidade = MINIMO_KWH_MAP.get(inputs_cliente["tipo_conexao"], 100)

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
        kwh_minimo_disponibilidade=kwh_minimo_disponibilidade
    )
    autoconsumo_atualizado = resultados_eco["autoconsumo_percent"]

    # 5. Calcular pagamento
    resultados_pag = renderizar_pagamento(
        valor_sistema_base=resultados_dim["valor_sistema_base"]
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
        kwh_minimo_disponibilidade=kwh_minimo_disponibilidade
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
    
    with st.expander("Opção de Administrador: Gerar PDF de Backup"):
        pdf_bytes = gerar_pdf_proposta(dados_pdf_final)
        st.download_button(
            label="📄 Gerar PDF (Backup)",
            data=pdf_bytes,
            file_name=f"Proposta_Brasil_Enertech_{dados_pdf_final['cliente']}.pdf",
            mime="application/pdf",
            use_container_width=True
        )

if __name__ == "__main__":
    main()