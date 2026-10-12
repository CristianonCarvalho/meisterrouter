package exemplo.lote;

/** O JSON de um item não tem o formato esperado. O lote registra o motivo e segue para o próximo. */
public class PedidoInvalido extends Exception {
    public PedidoInvalido(String mensagem) {
        super(mensagem);
    }
}
