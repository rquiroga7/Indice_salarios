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
profasis_path <- file.path(base_dir, "crudo_profasis.csv")
merged_path <- file.path(base_dir, "salarios_comparacion_2022_2026.csv")
plot_path <- file.path(base_dir, "salarios_comparacion_2022_2026.png")
profasis_index_path <- file.path(base_dir, "profasis_indice_base100_2022_2026.csv")
profasis_plot_path <- file.path(base_dir, "profasis_indice_base100_2022_2026.png")

indec <- read_csv2(indec_path, show_col_types = FALSE, locale = locale(decimal_mark = ",")) %>%
  mutate(
    fecha = dmy(periodo),
    indec_publico_nacional = as.numeric(v_m_subsector_publico_nacional)
  ) %>%
  select(fecha, indec_publico_nacional)

sinep_events <- read_csv(sinep_path, show_col_types = FALSE) %>%
  mutate(
    fecha = ymd(date),
    increase_pct = as.numeric(increase_pct)
  ) %>%
  select(event_order, fecha, increase_pct)

monthly_index <- tibble(fecha = seq.Date(as.Date("2022-01-01"), max(indec$fecha, na.rm = TRUE), by = "month"))

sinep_monthly <- monthly_index %>%
  left_join(sinep_events, by = "fecha") %>%
  arrange(fecha, event_order) %>%
  group_by(fecha) %>%
  summarise(
    sinep_increase = {
      values <- increase_pct[!is.na(increase_pct)]
      if (length(values) == 0) 0 else tail(values, 1)
    },
    .groups = "drop"
  )

plot_data <- monthly_index %>%
  left_join(indec, by = "fecha") %>%
  left_join(sinep_monthly, by = "fecha") %>%
  mutate(sinep_increase = coalesce(sinep_increase, 0))

write_csv(plot_data, merged_path)

plot <- ggplot(plot_data, aes(x = fecha)) +
  geom_col(aes(y = sinep_increase, fill = "SINEP UPCN"), width = 25, alpha = 0.45) +
  geom_line(aes(y = indec_publico_nacional, color = "INDEC sector publico nacional"), linewidth = 0.9) +
  geom_point(aes(y = indec_publico_nacional, color = "INDEC sector publico nacional"), size = 1.4) +
  scale_fill_manual(values = c("SINEP UPCN" = "#d97706")) +
  scale_color_manual(values = c("INDEC sector publico nacional" = "#0f766e")) +
  scale_y_continuous(
    labels = function(x) paste0(formatC(x, format = "f", digits = 1, decimal.mark = ","), "%")
  ) +
  scale_x_date(date_breaks = "3 months", date_labels = "%b\n%Y") +
  labs(
    title = "Variacion mensual: INDEC vs. SINEP UPCN",
    subtitle = "Sector publico nacional de INDEC y aumentos mensuales firmados en la paritaria SINEP (2022-2026)",
    x = NULL,
    y = "Variacion mensual",
    fill = NULL,
    color = NULL,
    caption = "Fuentes: INDEC y UPCN"
  ) +
  theme_minimal(base_size = 12) +
  theme(
    legend.position = "top",
    panel.grid.minor = element_blank(),
    plot.title = element_text(face = "bold"),
    axis.text.x = element_text(angle = 0, vjust = 0.5)
  )

ggsave(plot_path, plot, width = 12, height = 6, dpi = 160)

profasis_raw <- read_csv(
  profasis_path,
  show_col_types = FALSE,
  locale = locale(date_names = "es")
) %>%
  mutate(
    fecha = ymd(fecha),
    salario = as.numeric(salario)
  ) %>%
  filter(fecha >= as.Date("2022-01-01"), fecha <= as.Date("2026-03-01")) %>%
  arrange(fecha)

profasis_monthly <- tibble(fecha = seq.Date(min(profasis_raw$fecha), max(profasis_raw$fecha), by = "month")) %>%
  left_join(profasis_raw, by = "fecha") %>%
  arrange(fecha)

for (i in seq_len(nrow(profasis_monthly))) {
  if (is.na(profasis_monthly$salario[i]) && i > 1) {
    profasis_monthly$salario[i] <- profasis_monthly$salario[i - 1]
  }
}

profasis_index <- profasis_monthly %>%
  mutate(
    variacion_mensual = (salario / lag(salario) - 1) * 100,
    indice_base_100 = 100 * salario / first(salario)
  )

write_csv(profasis_index, profasis_index_path)

profasis_plot <- ggplot(profasis_index, aes(x = fecha, y = indice_base_100)) +
  geom_line(color = "#7c3aed", linewidth = 1) +
  geom_point(color = "#7c3aed", size = 1.4) +
  scale_x_date(date_breaks = "3 months", date_labels = "%b\n%Y") +
  scale_y_continuous(labels = function(x) formatC(x, format = "f", digits = 1, decimal.mark = ",")) +
  labs(
    title = "Indice salarial universitario PROFASIS",
    subtitle = "Base 100 en enero de 2022, construido desde crudo_profasis.csv",
    x = NULL,
    y = "Indice base 100",
    caption = "Fuente: crudo_profasis.csv"
  ) +
  theme_minimal(base_size = 12) +
  theme(
    panel.grid.minor = element_blank(),
    plot.title = element_text(face = "bold")
  )

ggsave(profasis_plot_path, profasis_plot, width = 12, height = 6, dpi = 160)

print(plot)
print(profasis_plot)