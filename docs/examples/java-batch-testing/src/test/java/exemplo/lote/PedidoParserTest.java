package exemplo.lote;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

/** Camada 1: sem rede, sem servidor. Roda em milissegundos. */
class PedidoParserTest {
    private final PedidoParser parser = new PedidoParser();
    private final ObjectMapper json = new ObjectMapper();

    private JsonNode no(String texto) throws IOException {
        return json.readTree(texto);
    }

    @Test
    void itemCompletoViraPedido() throws Exception {
        Pedido p = parser.parse(no("{\"id\":\"P1\",\"cliente\":\"Ana\",\"valor\":10.5,\"status\":\"ATIVO\"}"));
        assertEquals("P1", p.getId());
        assertEquals(0, new BigDecimal("10.5").compareTo(p.getValor()));
    }

    @Test
    void statusAusenteContaComoAtivo() throws Exception {
        assertEquals("ATIVO", parser.parse(no("{\"id\":\"P1\",\"cliente\":\"Ana\",\"valor\":1}")).getStatus());
    }

    @Test
    void valorNaoNumericoEhRejeitadoComMotivo() throws Exception {
        PedidoInvalido erro = assertThrows(PedidoInvalido.class,
                () -> parser.parse(no("{\"id\":\"P5\",\"cliente\":\"Eva\",\"valor\":\"abc\"}")));
        assertTrue(erro.getMessage().contains("P5"));
    }

    @Test
    void semIdEhRejeitado() throws Exception {
        assertThrows(PedidoInvalido.class, () -> parser.parse(no("{\"cliente\":\"Ana\",\"valor\":1}")));
    }

    @Test
    void descontoSoAcimaDeMil() {
        assertEquals("1000.00", PedidoParser.valorFinal(new BigDecimal("1000")).toPlainString());
        assertEquals("900.01", PedidoParser.valorFinal(new BigDecimal("1000.01")).toPlainString());
    }
}
