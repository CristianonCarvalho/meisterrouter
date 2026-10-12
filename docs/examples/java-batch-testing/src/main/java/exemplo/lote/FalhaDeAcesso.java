package exemplo.lote;

/** Falha de rede ou resposta inutilizável do site (timeout, conexão recusada, erro persistente). */
public class FalhaDeAcesso extends RuntimeException {
    public FalhaDeAcesso(String mensagem, Throwable causa) {
        super(mensagem, causa);
    }

    public FalhaDeAcesso(String mensagem) {
        super(mensagem);
    }
}
