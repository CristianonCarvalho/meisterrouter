package exemplo.lote;

import java.math.BigDecimal;

/** Um pedido já validado, como o lote o enxerga. */
public final class Pedido {
    private final String id;
    private final String cliente;
    private final BigDecimal valor;
    private final String status;

    public Pedido(String id, String cliente, BigDecimal valor, String status) {
        this.id = id;
        this.cliente = cliente;
        this.valor = valor;
        this.status = status;
    }

    public String getId() { return id; }
    public String getCliente() { return cliente; }
    public BigDecimal getValor() { return valor; }
    public String getStatus() { return status; }
}
