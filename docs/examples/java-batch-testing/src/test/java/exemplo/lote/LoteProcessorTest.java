package exemplo.lote;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.wireMockConfig;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.github.tomakehurst.wiremock.junit5.WireMockExtension;
import com.github.tomakehurst.wiremock.stubbing.Scenario;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;

/**
 * Camada 2: o lote inteiro contra um "site falso" (WireMock) em localhost, respondendo com JSON gravado.
 * Nenhum pedido sai da máquina; o site legado real nunca é tocado.
 */
class LoteProcessorTest {
    private static final String PAGINA1 = "/api/pedidos?pagina=1";
    private static final String PAGINA2 = "/api/pedidos?pagina=2";

    @RegisterExtension
    static WireMockExtension site = WireMockExtension.newInstance()
            .options(wireMockConfig().dynamicPort())
            .build();

    private static String json(String fixture) {
        return Fixtures.ler("fixtures/" + fixture);
    }

    private static com.github.tomakehurst.wiremock.client.ResponseDefinitionBuilder ok(String fixture) {
        return aResponse().withStatus(200).withHeader("Content-Type", "application/json").withBody(json(fixture));
    }

    private LoteProcessor lote(Saida saida) {
        SiteClient cliente = new SiteClient(site.baseUrl(), 1000, 1000);
        return new LoteProcessor(cliente, saida, "robo", "segredo", 0);
    }

    private static Saida coletar(final List<String> linhas) {
        return new Saida() {
            @Override
            public void gravar(String linha) {
                linhas.add(linha);
            }
        };
    }

    private void stubsNormais() {
        site.stubFor(post(urlEqualTo("/login")).willReturn(ok("login_ok.json")));
        site.stubFor(get(urlEqualTo(PAGINA1)).willReturn(ok("pedidos_pagina1.json")));
        site.stubFor(get(urlEqualTo(PAGINA2)).willReturn(ok("pedidos_pagina2.json")));
    }

    @Test
    void loteCompletoReproduzOGoldenMaster() {
        stubsNormais();
        List<String> linhas = new ArrayList<String>();

        Resumo resumo = lote(coletar(linhas)).processar();

        String esperado = Fixtures.ler("golden/saida_esperada.csv").trim();
        assertEquals(esperado, String.join("\n", linhas));
        assertEquals(2, resumo.getPaginas());
        assertEquals(4, resumo.getGravados());
        assertEquals(1, resumo.getRejeitados().size());
        assertTrue(resumo.getRejeitados().get(0).contains("P5"));
    }

    @Test
    void dryRunProcessaTudoMasNaoGrava() {
        stubsNormais();
        Resumo resumo = lote(Saida.DESCARTAR).processar();
        assertEquals(4, resumo.getGravados());
    }

    @Test
    void errosTemporarios5xxSaoRepetidos() {
        site.stubFor(post(urlEqualTo("/login")).willReturn(ok("login_ok.json")));
        site.stubFor(get(urlEqualTo(PAGINA2)).willReturn(ok("pedidos_pagina2.json")));
        site.stubFor(get(urlEqualTo(PAGINA1)).inScenario("instavel")
                .whenScenarioStateIs(Scenario.STARTED)
                .willReturn(aResponse().withStatus(503)).willSetStateTo("segunda"));
        site.stubFor(get(urlEqualTo(PAGINA1)).inScenario("instavel")
                .whenScenarioStateIs("segunda")
                .willReturn(ok("pedidos_pagina1.json")));

        Resumo resumo = lote(Saida.DESCARTAR).processar();

        assertEquals(4, resumo.getGravados());
        site.verify(2, getRequestedFor(urlEqualTo(PAGINA1)));
    }

    @Test
    void erroPersistenteDesistePorDepoisDeTresTentativas() {
        site.stubFor(post(urlEqualTo("/login")).willReturn(ok("login_ok.json")));
        site.stubFor(get(urlEqualTo(PAGINA1)).willReturn(aResponse().withStatus(500)));

        FalhaDeAcesso erro = assertThrows(FalhaDeAcesso.class, () -> lote(Saida.DESCARTAR).processar());

        assertTrue(erro.getMessage().contains("persistente"));
        site.verify(3, getRequestedFor(urlEqualTo(PAGINA1)));
    }

    @Test
    void sessaoExpiradaFazNovoLoginEContinua() {
        site.stubFor(post(urlEqualTo("/login")).inScenario("sessao")
                .whenScenarioStateIs(Scenario.STARTED)
                .willReturn(ok("login_ok.json")).willSetStateTo("relogado"));
        site.stubFor(post(urlEqualTo("/login")).inScenario("sessao")
                .whenScenarioStateIs("relogado")
                .willReturn(ok("login_ok_2.json")));
        site.stubFor(get(urlEqualTo(PAGINA1)).withHeader("Authorization", equalTo("Bearer tok-123"))
                .willReturn(aResponse().withStatus(401)));
        site.stubFor(get(urlEqualTo(PAGINA1)).withHeader("Authorization", equalTo("Bearer tok-456"))
                .willReturn(ok("pedidos_pagina1.json")));
        site.stubFor(get(urlEqualTo(PAGINA2)).withHeader("Authorization", equalTo("Bearer tok-456"))
                .willReturn(ok("pedidos_pagina2.json")));

        Resumo resumo = lote(Saida.DESCARTAR).processar();

        assertEquals(4, resumo.getGravados());
        site.verify(2, postRequestedFor(urlEqualTo("/login")));
    }

    @Test
    void loginRecusadoInterrompeOLote() {
        site.stubFor(post(urlEqualTo("/login")).willReturn(aResponse().withStatus(401)));
        FalhaDeAcesso erro = assertThrows(FalhaDeAcesso.class, () -> lote(Saida.DESCARTAR).processar());
        assertTrue(erro.getMessage().contains("login recusado"));
    }

    @Test
    void siteLentoEstouraOTimeoutSemTravarOLote() {
        site.stubFor(post(urlEqualTo("/login")).willReturn(ok("login_ok.json")));
        site.stubFor(get(urlEqualTo(PAGINA1)).willReturn(ok("pedidos_pagina1.json").withFixedDelay(1500)));
        SiteClient cliente = new SiteClient(site.baseUrl(), 500, 300);
        LoteProcessor impaciente = new LoteProcessor(cliente, Saida.DESCARTAR, "robo", "segredo", 0);

        assertThrows(FalhaDeAcesso.class, impaciente::processar);
    }

    @Test
    void jsonTruncadoViraErroClaroEmVezDeLixo() {
        site.stubFor(post(urlEqualTo("/login")).willReturn(ok("login_ok.json")));
        site.stubFor(get(urlEqualTo(PAGINA1)).willReturn(ok("pedidos_truncado.json")));

        FalhaDeAcesso erro = assertThrows(FalhaDeAcesso.class, () -> lote(Saida.DESCARTAR).processar());

        assertTrue(erro.getMessage().contains("JSON válido"));
    }
}
