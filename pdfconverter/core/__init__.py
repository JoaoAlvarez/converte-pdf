"""Motor de conversão — funções puras, sem dependência da interface.

Cada função recebe um callback opcional ``progress(atual, total, mensagem)``
para reportar o andamento à interface. As importações pesadas são feitas
dentro das funções para deixar a inicialização do programa mais rápida.
"""
