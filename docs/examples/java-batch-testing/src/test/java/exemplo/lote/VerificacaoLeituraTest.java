package exemplo.lote;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * Detector de mudança de layout no site REAL. Só faz GET (leitura), não altera nada e fica fora do `mvn test`:
 * roda apenas com `mvn test -Pverificacao-leitura -Dsite.url=https://... -Dsite.token=...`.
 * Sem as duas propriedades, o teste é ignorado.
 */
@Tag("producao-leitura")
class VerificacaoLeituraTest {

    @Test
    void siteRealAindaDevolveOFormatoEsperado() throws Exception {
        String url = System.getProperty("site.url");
        String token = System.getProperty("site.token");
        assumeTrue(url != null && token != null, "informe -Dsite.url e -Dsite.token para rodar");

        Resposta resposta = new SiteClient(url, 5000, 10000).get("/api/pedidos?pagina=1", token);

        assertEquals(200, resposta.getStatus());
        JsonNode corpo = new ObjectMapper().readTree(resposta.getCorpo());
        assertTrue(corpo.has("totalPaginas"), "campo 'totalPaginas' sumiu");
        assertTrue(corpo.path("itens").isArray(), "campo 'itens' não é lista");
        for (JsonNode item : corpo.path("itens")) {
            assertTrue(item.hasNonNull("id") && item.hasNonNull("cliente"), "item sem id/cliente");
            assertTrue(item.path("valor").isNumber(), "item com 'valor' não numérico");
        }
    }
}
