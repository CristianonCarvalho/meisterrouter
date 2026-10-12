package exemplo.lote;

import com.fasterxml.jackson.databind.JsonNode;
import java.math.BigDecimal;

/**
 * Converte um item JSON em {@link Pedido}. Não faz rede: recebe a árvore já lida, por isso se testa
 * só com arquivos de fixture. É aqui que mora o risco de o site legado mudar o formato.
 */
public class PedidoParser {

    public Pedido parse(JsonNode item) throws PedidoInvalido {
        String id = texto(item, "id");
        String cliente = texto(item, "cliente");
        JsonNode valorNo = item.get("valor");
        if (valorNo == null || !valorNo.isNumber()) {
            throw new PedidoInvalido("pedido " + id + ": campo 'valor' ausente ou não numérico");
        }
        String status = item.hasNonNull("status") ? item.get("status").asText() : "ATIVO";
        return new Pedido(id, cliente, valorNo.decimalValue(), status);
    }

    private static String texto(JsonNode item, String campo) throws PedidoInvalido {
        JsonNode no = item.get(campo);
        if (no == null || no.isNull() || no.asText().trim().isEmpty()) {
            throw new PedidoInvalido("campo '" + campo + "' ausente");
        }
        return no.asText();
    }

    /** Regra de negócio do exemplo: pedidos acima de 1000 recebem 10% de desconto. */
    public static BigDecimal valorFinal(BigDecimal valor) {
        if (valor.compareTo(new BigDecimal("1000")) > 0) {
            return valor.multiply(new BigDecimal("0.90")).setScale(2, java.math.RoundingMode.HALF_UP);
        }
        return valor.setScale(2, java.math.RoundingMode.HALF_UP);
    }
}
