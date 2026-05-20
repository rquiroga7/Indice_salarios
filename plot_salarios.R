library(readr)
library(dplyr)
library(lubridate)
library(ggplot2)

script_file <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
base_dir <- if (length(script_file) > 0) {
  dirname(normalizePath(sub("^--file=", "", script_file[1])))
} else {
  getwd()
}

indec_path <- file.path(base_dir, "variacion_indice_salarios.csv")
sinep_path <- file.path(base_dir, "sinep_upcn_2022_2026.csv")
universitarios_path <- file.path(base_dir, "crudo_profasis.csv")
defensa_path <- file.path(base_dir, "defensa_ffaa_2022_2026.csv")
seguridad_justicia_path <- file.path(base_dir, "judicial_csjn_2022_2026.csv")

componentes_csv <- file.path(base_dir, "salarios_componentes_2022_2026.csv")
barras_path <- file.path(base_dir, "salarios_componentes_variacion_barras_2022_2026.png")
lineas_path <- file.path(base_dir, "salarios_componentes_indices_lineas_2022_2026.png")
comparacion_path <- file.path(base_dir, "indice_indec_vs_calculado_rebase_2023_11_2026_03.png")
variacion_2026_path <- file.path(base_dir, "variacion_mensual_2026_comparativa.png")

leer_profasis <- function(path) {
  raw <- read_csv(path, show_col_types = FALSE) %>%
    mutate(
      fecha = ymd(fecha),
      salario = as.numeric(salario)
    ) %>%
    filter(fecha >= as.Date("2022-01-01"), fecha <= as.Date("2026-12-01")) %>%
    arrange(fecha)

  monthly <- tibble(fecha = seq.Date(min(raw$fecha), max(raw$fecha), by = "month")) %>%
    left_join(raw, by = "fecha") %>%
    arrange(fecha)

  for (i in seq_len(nrow(monthly))) {
    if (is.na(monthly$salario[i]) && i > 1) {
      monthly$salario[i] <- monthly$salario[i - 1]
    }
  }

  monthly %>%
    mutate(universitarios = (salario / lag(salario) - 1) * 100) %>%
    select(fecha, universitarios)
}

base100_from_variation <- function(var_pct) {
  if (length(var_pct) == 0) {
    return(numeric(0))
  }
  index <- numeric(length(var_pct))
  index[1] <- 100
  if (length(var_pct) > 1) {
    for (i in 2:length(var_pct)) {
      index[i] <- index[i - 1] * (1 + var_pct[i] / 100)
    }
  }
  index
}

pick_event_value <- function(values) {
  values <- values[!is.na(values) & values != 0]
  if (length(values) == 0) {
    0
  } else {
    values[1]
  }
}

indec <- read_csv2(indec_path, show_col_types = FALSE, locale = locale(decimal_mark = ",")) %>%
  mutate(
    fecha = dmy(periodo),
    indec_publico_nacional = as.numeric(v_m_subsector_publico_nacional)
  ) %>%
  select(fecha, indec_publico_nacional)

sinep <- read_csv(sinep_path, show_col_types = FALSE) %>%
  mutate(
    fecha = ymd(date),
    sinep = as.numeric(increase_pct)
  ) %>%
  select(fecha, sinep) %>%
  group_by(fecha) %>%
  summarise(sinep = dplyr::last(na.omit(sinep)), .groups = "drop")

universitarios <- leer_profasis(universitarios_path)

defensa <- read_csv(defensa_path, show_col_types = FALSE) %>%
  mutate(
    fecha = as.Date(sprintf("%04d-%02d-01", as.integer(effective_year), as.integer(effective_month))),
    defensa = as.numeric(increase_pct)
  ) %>%
  select(fecha, defensa) %>%
  group_by(fecha) %>%
  summarise(defensa = pick_event_value(defensa), .groups = "drop")

seguridad_justicia <- read_csv(seguridad_justicia_path, show_col_types = FALSE) %>%
  mutate(
    fecha = ymd(date),
    seguridad_y_justicia = as.numeric(increase_pct)
  ) %>%
  select(fecha, seguridad_y_justicia) %>%
  group_by(fecha) %>%
  summarise(seguridad_y_justicia = pick_event_value(seguridad_y_justicia), .groups = "drop")

fechas <- sort(unique(c(indec$fecha, sinep$fecha, universitarios$fecha, defensa$fecha, seguridad_justicia$fecha)))
fechas <- fechas[fechas >= as.Date("2022-01-01") & fechas <= as.Date("2026-12-01")]

mensual <- tibble(fecha = seq.Date(min(fechas), max(fechas), by = "month")) %>%
  left_join(indec, by = "fecha") %>%
  left_join(sinep, by = "fecha") %>%
  left_join(universitarios, by = "fecha") %>%
  left_join(defensa, by = "fecha") %>%
  left_join(seguridad_justicia, by = "fecha") %>%
  mutate(
    indec_publico_nacional = coalesce(indec_publico_nacional, 0),
    sinep = coalesce(sinep, 0),
    universitarios = coalesce(universitarios, 0),
    defensa = coalesce(defensa, 0),
    seguridad_y_justicia = coalesce(seguridad_y_justicia, 0),
    indice_salarial_calculado_variacion = (2.11 * sinep + 2.18 * universitarios + 1.02 * defensa + 1.97 * seguridad_y_justicia) / 7.28,
    indec_indice_base100 = base100_from_variation(indec_publico_nacional),
    sinep_indice_base100 = base100_from_variation(sinep),
    universitarios_indice_base100 = base100_from_variation(universitarios),
    defensa_indice_base100 = base100_from_variation(defensa),
    seguridad_y_justicia_indice_base100 = base100_from_variation(seguridad_y_justicia),
    indice_salarial_calculado = (2.11 * sinep_indice_base100 + 2.18 * universitarios_indice_base100 + 1.02 * defensa_indice_base100 + 1.97 * seguridad_y_justicia_indice_base100) / 7.28
  )

write_csv(mensual, componentes_csv)

bar_long <- bind_rows(
  mensual %>% transmute(fecha, serie = "INDEC sector publico nacional", valor = indec_publico_nacional),
  mensual %>% transmute(fecha, serie = "SINEP UPCN", valor = sinep),
  mensual %>% transmute(fecha, serie = "Universitarios", valor = universitarios),
  mensual %>% transmute(fecha, serie = "Defensa", valor = defensa),
  mensual %>% transmute(fecha, serie = "Seguridad y justicia", valor = seguridad_y_justicia),
  mensual %>% transmute(fecha, serie = "Indice salarial calculado", valor = indice_salarial_calculado_variacion)
)

line_long <- bind_rows(
  mensual %>% transmute(fecha, serie = "INDEC real", valor = indec_indice_base100),
  mensual %>% transmute(fecha, serie = "SINEP UPCN", valor = sinep_indice_base100),
  mensual %>% transmute(fecha, serie = "Universitarios", valor = universitarios_indice_base100),
  mensual %>% transmute(fecha, serie = "Defensa", valor = defensa_indice_base100),
  mensual %>% transmute(fecha, serie = "Seguridad y justicia", valor = seguridad_y_justicia_indice_base100),
  mensual %>% transmute(fecha, serie = "Indice salarial calculado", valor = indice_salarial_calculado)
)

plot_barras <- ggplot(bar_long, aes(x = fecha, y = valor)) +
  geom_col(fill = "#4f46e5", alpha = 0.8, width = 25) +
  facet_wrap(~serie, ncol = 1, scales = "free_y") +
  scale_x_date(limits = c(as.Date("2023-11-01"), as.Date("2026-03-01")), date_breaks = "6 months", date_labels = "%b\n%Y") +
  labs(
    title = "Variacion mensual de los indices salariales",
    subtitle = "INDEC, SINEP, universitarios, defensa, seguridad y justicia e indice salarial calculado",
    x = NULL,
    y = "Variacion mensual (%)",
    caption = "Fuente calculada a partir de INDEC, UPCN, PROFASIS, Defensa y CSJN"
  ) +
  theme_minimal(base_size = 11) +
  theme(
    panel.grid.minor = element_blank(),
    strip.text = element_text(face = "bold"),
    plot.title = element_text(face = "bold")
  )

plot_lineas <- ggplot(line_long, aes(x = fecha, y = valor, color = serie)) +
  geom_line(aes(linetype = serie), linewidth = 0.9) +
  scale_color_manual(values = c(
    "INDEC real" = "#0f766e",
    "SINEP UPCN" = "#d97706",
    "Universitarios" = "#7c3aed",
    "Defensa" = "#2563eb",
    "Seguridad y justicia" = "#dc2626",
    "Indice salarial calculado" = "#0f766e"
  )) +
  scale_linetype_manual(values = c(
    "INDEC real" = "solid",
    "SINEP UPCN" = "solid",
    "Universitarios" = "solid",
    "Defensa" = "solid",
    "Seguridad y justicia" = "solid",
    "Indice salarial calculado" = "dashed"
  )) +
  scale_x_date(date_breaks = "6 months", date_labels = "%b\n%Y") +
  scale_y_continuous(labels = function(x) formatC(x, format = "f", digits = 1, decimal.mark = ",")) +
  labs(
    title = "Indices salariales base 100",
    subtitle = "Comparacion de componentes, indice real de INDEC e indice salarial calculado",
    x = NULL,
    y = "Base 100",
    color = NULL,
    linetype = NULL,
    caption = "Fuentes: INDEC, UPCN, PROFASIS, Boletin Oficial y serie oficial de Seguridad y Justicia"
  ) +
  theme_minimal(base_size = 11) +
  theme(
    panel.grid.minor = element_blank(),
    legend.position = "bottom",
    plot.title = element_text(face = "bold")
  )

ggsave(barras_path, plot_barras, width = 12, height = 12, dpi = 160)
ggsave(lineas_path, plot_lineas, width = 12, height = 7, dpi = 160)

comparacion_base <- as.Date("2023-11-01")
comparacion_fin <- as.Date("2026-03-01")
comparacion <- mensual %>%
  filter(fecha >= comparacion_base, fecha <= comparacion_fin) %>%
  mutate(
    indec_rebase = 100 * indec_indice_base100 / first(indec_indice_base100),
    calculado_rebase = 100 * indice_salarial_calculado / first(indice_salarial_calculado)
  )

comparacion_long <- bind_rows(
  comparacion %>% transmute(fecha, serie = "Indice salarial publico nacional - INDEC", valor = indec_rebase),
  comparacion %>% transmute(fecha, serie = "Indice calculado", valor = calculado_rebase)
)

plot_comparacion <- ggplot(comparacion_long, aes(x = fecha, y = valor, color = serie)) +
  geom_line(aes(linetype = serie), linewidth = 1) +
  scale_color_manual(values = c(
    "Indice salarial publico nacional - INDEC" = "#0f766e",
    "Indice calculado" = "#0f766e"
  )) +
  scale_linetype_manual(values = c(
    "Indice salarial publico nacional - INDEC" = "solid",
    "Indice calculado" = "dashed"
  )) +
  scale_x_date(limits = c(comparacion_base, comparacion_fin), date_breaks = "3 months", date_labels = "%b\n%Y") +
  scale_y_continuous(labels = function(x) formatC(x, format = "f", digits = 1, decimal.mark = ",")) +
  labs(
    title = "INDEC real vs indice calculado",
    subtitle = "Base 100 en noviembre de 2023, de noviembre de 2023 a marzo de 2026",
    x = NULL,
    y = "Base 100",
    color = NULL,
    linetype = NULL,
    caption = "Comparacion rebased a 2023-11-01"
  ) +
  theme_minimal(base_size = 11) +
  theme(
    panel.grid.minor = element_blank(),
    legend.position = "bottom",
    plot.title = element_text(face = "bold")
  )

ggsave(comparacion_path, plot_comparacion, width = 11, height = 6, dpi = 160)

variacion_2026 <- mensual %>%
  filter(fecha >= as.Date("2025-10-01"), fecha <= as.Date("2026-03-01")) %>%
  transmute(
    fecha,
    `Índice público nacional - INDEC` = indec_publico_nacional,
    `SINEP UPCN` = sinep,
    `Universitarios` = universitarios,
    `Defensa` = defensa,
    `Seguridad y justicia` = seguridad_y_justicia,
    `Indice calculado` = indice_salarial_calculado_variacion
  ) %>%
  tidyr::pivot_longer(-fecha, names_to = "serie", values_to = "valor") %>%
  mutate(
    mes = factor(format(fecha, "%b\n%Y"), levels = format(sort(unique(fecha)), "%b\n%Y")),
    serie = factor(
      serie,
      levels = c(
        "Índice público nacional - INDEC",
        "Indice calculado",
        "SINEP UPCN",
        "Universitarios",
        "Defensa",
        "Seguridad y justicia"
      )
    )
  )

plot_variacion_2026 <- ggplot(variacion_2026, aes(x = mes, y = valor, fill = serie)) +
  geom_col(position = position_dodge(width = 0.85), width = 0.75) +
  scale_fill_manual(values = c(
    "Índice público nacional - INDEC" = "#000000",
    "Indice calculado" = "#6b7280",
    "SINEP UPCN" = "#d97706",
    "Universitarios" = "#7c3aed",
    "Defensa" = "#2563eb",
    "Seguridad y justicia" = "#dc2626"
  )) +
  scale_y_continuous(labels = function(x) formatC(x, format = "f", digits = 1, decimal.mark = ",")) +
  labs(
    title = "Aumentos salariales mensuales de 2026",
    subtitle = "Variacion mensual por categoria: INDEC, SINEP, universitarios, defensa, seguridad y justicia e indice calculado",
    x = NULL,
    y = "Variacion mensual (%)",
    fill = NULL,
    caption = "Octubre de 2025 a marzo de 2026"
  ) +
  theme_minimal(base_size = 11) +
  theme(
    panel.grid.minor = element_blank(),
    axis.text.x = element_text(angle = 45, hjust = 1),
    legend.position = "bottom",
    plot.title = element_text(face = "bold")
  )

ggsave(variacion_2026_path, plot_variacion_2026, width = 13, height = 7, dpi = 160)

print(plot_barras)
print(plot_lineas)
print(plot_comparacion)
print(plot_variacion_2026)