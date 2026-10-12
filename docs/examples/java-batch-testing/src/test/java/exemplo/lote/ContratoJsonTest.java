package exemplo.lote;

import org.junit.jupiter.api.Test;
import org.skyscreamer.jsonassert.JSONAssert;
import org.skyscreamer.jsonassert.JSONCompareMode;

/**
 * Camada 3: o contrato. Confere que a resposta gravada do site ainda tem os campos que o lote lê. A ordem dos
 * campos e campos extras não quebram (modo LENIENT); campo sumido ou de outro tipo, sim.
 */
class ContratoJsonTest {

    @Test
    void paginaDePedidosTemOsCamposQueOLoteUsa() throws Exception {
        // LENIENT aceita campos extras e ordem diferente, mas a lista precisa ter o mesmo tamanho.
        String esperado = "{\"totalPaginas\":2,\"itens\":["
                + "{\"id\":\"P1\",\"cliente\":\"Ana\",\"valor\":500.0},"
                + "{\"id\":\"P2\",\"cliente\":\"Bruno\",\"valor\":2000.0},"
                + "{\"id\":\"P3\",\"cliente\":\"Carla\",\"valor\":300.0}]}";
        JSONAssert.assertEquals(esperado, Fixtures.ler("fixtures/pedidos_pagina1.json"), JSONCompareMode.LENIENT);
    }
}
