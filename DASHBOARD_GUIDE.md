# 📊 Дашборд Grafana для Radius Core

Подробное руководство по созданию комплексного дашборда для мониторинга производительности и состояния системы Radius Core.

## 🎯 Обзор метрик

### HTTP метрики
- **Время выполнения запросов** - `http_request_duration_seconds`
- **Количество запросов** - `http_requests_total`
- **RPS (Requests Per Second)** - `http_requests_per_second_total`
- **Запросы по времени** - `http_requests_by_hour_total`, `http_requests_by_minute_total`, `http_requests_by_weekday_total`

### Redis метрики
- **Время операций** - `redis_operation_duration_seconds`
- **Количество операций** - `redis_operations_total`

### RabbitMQ метрики
- **Время операций** - `rabbitmq_operation_duration_seconds`
- **Количество операций** - `rabbitmq_operations_total`

### RADIUS метрики
- **Время функций** - `radius_function_duration_seconds`
- **Количество вызовов** - `radius_function_total`

## 🚀 Создание дашборда

### 1. Создание нового дашборда

1. Откройте Grafana
2. Нажмите **"+"** → **"Dashboard"**
3. Название: `Radius Core - Production Dashboard`
4. Описание: `Комплексный мониторинг производительности Radius Core`

### 2. Настройка источника данных

Убедитесь, что Prometheus подключен как источник данных:
- **Type**: Prometheus
- **URL**: `http://your-prometheus:9090`
- **Access**: Server (default)

## 📈 Панели дашборда

### Панель 1: Общий статус системы

**Тип**: Stat
**Заголовок**: System Status
**Query**:
```promql
# Общее количество HTTP запросов
sum(http_requests_total) by (endpoint)
```

**Поля**:
- **Value**: `sum(http_requests_total)`
- **Unit**: `short`
- **Color mode**: `background`
- **Thresholds**: 
  - 0-100: green
  - 100-1000: yellow
  - 1000+: red

### Панель 2: RPS (Requests Per Second)

**Тип**: Time series
**Заголовок**: HTTP Requests Per Second
**Query**:
```promql
# RPS по endpoint'ам
rate(http_requests_per_second_total[5m])
```

**Поля**:
- **Legend**: `{{endpoint}} - {{method}}`
- **Y-axis**: 
  - **Unit**: `reqps`
  - **Min**: 0
- **Thresholds**: 
  - 0-10: green
  - 10-50: yellow
  - 50+: red

### Панель 3: Время выполнения HTTP запросов

**Тип**: Time series
**Заголовок**: HTTP Request Duration
**Query**:
```promql
# 95-й процентиль времени выполнения
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
```

**Поля**:
- **Legend**: `{{endpoint}} - P95`
- **Y-axis**: 
  - **Unit**: `s`
  - **Min**: 0
- **Thresholds**: 
  - 0-0.1: green
  - 0.1-0.5: yellow
  - 0.5+: red

### Панель 4: Heatmap по часам

**Тип**: Heatmap
**Заголовок**: Request Heatmap by Hour
**Query**:
```promql
# RPS по часам для создания heatmap
rate(http_requests_by_hour_total[5m])
```

**Поля**:
- **Format as**: Heatmap
- **Y-axis**: `hour` label
- **X-axis**: время
- **Color mode**: Scheme
- **Legend**: `{{endpoint}} - Hour {{hour}}`

### Панель 5: Heatmap по минутам

**Тип**: Heatmap
**Заголовок**: Request Heatmap by Minute
**Query**:
```promql
# RPS по минутам для детального heatmap
rate(http_requests_by_minute_total[5m])
```

**Поля**:
- **Format as**: Heatmap
- **Y-axis**: `minute` label (0-59)
- **X-axis**: время
- **Color mode**: Scheme
- **Legend**: `{{endpoint}} - Minute {{minute}}`

### Панель 6: Запросы по дням недели

**Тип**: Bar chart
**Заголовок**: Requests by Day of Week
**Query**:
```promql
# Общее количество запросов по дням недели
sum(http_requests_by_weekday_total) by (weekday)
```

**Поля**:
- **X-axis**: `weekday`
- **Y-axis**: количество запросов
- **Legend**: `{{weekday}}`

### Панель 7: Redis производительность

**Тип**: Time series
**Заголовок**: Redis Operations Performance
**Query**:
```promql
# Время Redis операций
rate(redis_operation_duration_seconds_sum[5m]) / rate(redis_operation_duration_seconds_count[5m])
```

**Поля**:
- **Legend**: `{{operation}} - Avg Duration`
- **Y-axis**: 
  - **Unit**: `s`
  - **Min**: 0
- **Thresholds**: 
  - 0-0.01: green
  - 0.01-0.1: yellow
  - 0.1+: red

### Панель 8: Redis операции в секунду

**Тип**: Time series
**Заголовок**: Redis Operations Per Second
**Query**:
```promql
# RPS для Redis операций
rate(redis_operations_total[5m])
```

**Поля**:
- **Legend**: `{{operation}} - {{status}}`
- **Y-axis**: 
  - **Unit**: `ops`
  - **Min**: 0

### Панель 9: RabbitMQ производительность

**Тип**: Time series
**Заголовок**: RabbitMQ Operations Performance
**Query**:
```promql
# Время RabbitMQ операций
rate(rabbitmq_operation_duration_seconds_sum[5m]) / rate(rabbitmq_operation_duration_seconds_count[5m])
```

**Поля**:
- **Legend**: `{{operation}} - {{queue}} - Avg Duration`
- **Y-axis**: 
  - **Unit**: `s`
  - **Min**: 0
- **Thresholds**: 
  - 0-0.01: green
  - 0.01-0.1: yellow
  - 0.1+: red

### Панель 10: RabbitMQ операции в секунду

**Тип**: Time series
**Заголовок**: RabbitMQ Operations Per Second
**Query**:
```promql
# RPS для RabbitMQ операций
rate(rabbitmq_operations_total[5m])
```

**Поля**:
- **Legend**: `{{operation}} - {{queue}} - {{status}}`
- **Y-axis**: 
  - **Unit**: `ops`
  - **Min**: 0

### Панель 11: RADIUS функции производительность

**Тип**: Time series
**Заголовок**: RADIUS Functions Performance
**Query**:
```promql
# Время RADIUS функций
rate(radius_function_duration_seconds_sum[5m]) / rate(radius_function_duration_seconds_count[5m])
```

**Поля**:
- **Legend**: `{{function}} - Avg Duration`
- **Y-axis**: 
  - **Unit**: `s`
  - **Min**: 0
- **Thresholds**: 
  - 0-0.1: green
  - 0.1-0.5: yellow
  - 0.5+: red

### Панель 12: RADIUS функции в секунду

**Тип**: Time series
**Заголовок**: RADIUS Functions Per Second
**Query**:
```promql
# RPS для RADIUS функций
rate(radius_function_total[5m])
```

**Поля**:
- **Legend**: `{{function}} - {{status}}`
- **Y-axis**: 
  - **Unit**: `ops`
  - **Min**: 0

## 🔧 Дополнительные настройки

### Переменные дашборда

Добавьте переменные для фильтрации:

1. **endpoint_filter**:
   - **Type**: Query
   - **Query**: `label_values(http_requests_total, endpoint)`
   - **Label**: Endpoint
   - **Multi-value**: true

2. **method_filter**:
   - **Type**: Query**
   - **Query**: `label_values(http_requests_total, method)`
   - **Label**: HTTP Method
   - **Multi-value**: true

3. **time_range**:
   - **Type**: Interval
   - **Label**: Time Range
   - **Default**: 1h

### Обновление панелей

Используйте переменные в запросах:
```promql
# Пример с фильтром по endpoint
rate(http_requests_total{endpoint=~"$endpoint_filter"}[5m])
```

## 📊 Алерты

### Критические алерты

1. **Высокий RPS**:
   ```promql
   rate(http_requests_per_second_total[5m]) > 100
   ```

2. **Медленные HTTP запросы**:
   ```promql
   histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 1
   ```

3. **Медленные Redis операции**:
   ```promql
   rate(redis_operation_duration_seconds_sum[5m]) / rate(redis_operation_duration_seconds_count[5m]) > 0.1
   ```

### Предупреждения

1. **Средний RPS**:
   ```promql
   rate(http_requests_per_second_total[5m]) > 50
   ```

2. **Среднее время HTTP запросов**:
   ```promql
   histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 0.5
   ```

## 🎨 Стилизация

### Цветовая схема
- **Зеленый**: Нормальная производительность
- **Желтый**: Средняя нагрузка
- **Красный**: Высокая нагрузка/проблемы

### Размеры панелей
- **Stat панели**: 6x4
- **Time series**: 12x6
- **Heatmap**: 12x8
- **Bar chart**: 8x6

## 📱 Мобильная версия

Создайте отдельную версию дашборда для мобильных устройств:
- Уменьшите размеры панелей
- Используйте только критически важные метрики
- Оптимизируйте легенды

## 🔄 Автоматизация

### Экспорт/импорт
1. Сохраните дашборд как JSON
2. Используйте Grafana API для автоматического развертывания
3. Настройте версионирование через Git

### Обновление метрик
- Проверяйте новые метрики каждые 2 недели
- Добавляйте новые панели по мере необходимости
- Оптимизируйте запросы для производительности

## 📚 Полезные ссылки

- [Prometheus Query Language](https://prometheus.io/docs/prometheus/latest/querying/)
- [Grafana Heatmap Documentation](https://grafana.com/docs/grafana/latest/visualizations/heatmap/)
- [Prometheus Histogram Quantiles](https://prometheus.io/docs/practices/histograms/)

## 🎯 Заключение

Этот дашборд предоставляет полную картину производительности Radius Core:
- **Мониторинг в реальном времени** всех компонентов
- **Heatmap** для анализа паттернов нагрузки
- **Алерты** для предотвращения проблем
- **Исторические данные** для планирования ресурсов

Регулярно анализируйте данные и оптимизируйте систему на основе полученных метрик!
