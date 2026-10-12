package exemplo.lote;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;

/**
 * Lote de exemplo: faz login, lê os pedidos página a página, aplica a regra e grava uma linha por pedido ativo.
 * Resiliência testada com WireMock: erro 5xx (repete), sessão expirada (novo login) e item malformado (rejeita e segue).
 */
public class LoteProcessor {
    private static final int TENTATIVAS = 3;

    private final SiteClient site;
    private final Saida saida;
    private final String usuario;
    private final String senha;
    private final long esperaEntreTentativasMs;
    private final ObjectMapper json = new ObjectMapper();
    private final PedidoParser parser = new PedidoParser();
    private String token;

    public LoteProcessor(SiteClient site, Saida saida, String usuario, String senha, long esperaEntreTentativasMs) {
        this.site = site;
        this.saida = saida;
        this.usuario = usuario;
        this.senha = senha;
        this.esperaEntreTentativasMs = esperaEntreTentativasMs;
    }

    public Resumo processar() {
        Resumo resumo = new Resumo();
        login();
        int pagina = 1;
        int totalPaginas = 1;
        while (pagina <= totalPaginas) {
            JsonNode corpo = lerJson(buscar("/api/pedidos?pagina=" + pagina));
            totalPaginas = corpo.path("totalPaginas").asInt(1);
            resumo.paginaLida();
            for (JsonNode item : corpo.path("itens")) {
                tratar(item, resumo);
            }
            pagina++;
        }
        return resumo;
    }

    private void tratar(JsonNode item, Resumo resumo) {
        try {
            Pedido pedido = parser.parse(item);
            if (!"ATIVO".equals(pedido.getStatus())) {
                return;
            }
            saida.gravar(pedido.getId() + ";" + pedido.getCliente() + ";"
                    + PedidoParser.valorFinal(pedido.getValor()).toPlainString());
            resumo.gravado();
        } catch (PedidoInvalido e) {
            resumo.rejeitado(e.getMessage());
        }
    }

    private void login() {
        Resposta resposta = site.post("/login", "{\"usuario\":\"" + usuario + "\",\"senha\":\"" + senha + "\"}");
        if (resposta.getStatus() != 200) {
            throw new FalhaDeAcesso("login recusado: HTTP " + resposta.getStatus());
        }
        token = lerJson(resposta).path("token").asText();
        if (token.isEmpty()) {
            throw new FalhaDeAcesso("login sem token na resposta");
        }
    }

    /** GET com repetição em 5xx e um novo login quando o site responde 401 (sessão expirada). */
    private Resposta buscar(String caminho) {
        boolean reloginFeito = false;
        for (int tentativa = 1; tentativa <= TENTATIVAS; tentativa++) {
            Resposta resposta = site.get(caminho, token);
            if (resposta.getStatus() == 200) {
                return resposta;
            }
            if (resposta.getStatus() == 401 && !reloginFeito) {
                reloginFeito = true;
                login();
                tentativa--;
                continue;
            }
            if (resposta.getStatus() < 500) {
                throw new FalhaDeAcesso(caminho + ": HTTP " + resposta.getStatus());
            }
            esperar();
        }
        throw new FalhaDeAcesso(caminho + ": erro persistente após " + TENTATIVAS + " tentativas");
    }

    private void esperar() {
        if (esperaEntreTentativasMs <= 0) {
            return;
        }
        try {
            Thread.sleep(esperaEntreTentativasMs);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private JsonNode lerJson(Resposta resposta) {
        try {
            return json.readTree(resposta.getCorpo());
        } catch (IOException e) {
            throw new FalhaDeAcesso("resposta não é JSON válido: " + e.getMessage(), e);
        }
    }
}
