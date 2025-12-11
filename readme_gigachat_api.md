# Авторизация

[source](https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/gigachat-api.md)

<Heading as={"h1"} className={"openapi__heading"} children={"Авторизация"} />

Справочная документация по REST API нейросетевой модели GigaChat.

О стоимости и условиях использования GigaChat API вы можете узнать в разделе [Тарифы и оплата](/ru/gigachat/api/tariffs).

## Получение токена доступа и авторизация запросов

Запросы к GigaChat API передаются по адресу `https://gigachat.devices.sberbank.ru/` и авторизуются с помощью токена доступа по протоколу [OAuth 2.0](https://tools.ietf.org/html/rfc6749).
Токен доступа передается в заголовке `Authorization`:

```sh
curl -L -X GET 'https://gigachat.devices.sberbank.ru/api/v1/models' \
-H 'Accept: application/json' \
-H 'Authorization: Bearer <токен_доступа>'
```

:::tip

Вы также можете передавать запросы к [моделям в раннем доступе](/ru/gigachat/models/preview-models).
Их возможности могут отличаться от моделей, доступных в промышленном контуре.

:::

Чтобы получить токен, отправьте запрос <APIMethod type="POST" path="/api/v2/oauth" link="/ru/gigachat/api/reference/rest/post-token" />:

```sh
curl -L -X POST 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth' \
-H 'Content-Type: application/x-www-form-urlencoded' \
-H 'Accept: application/json' \
-H 'RqUID: <идентификатор_запроса>' \
-H 'Authorization: Basic ключ_авторизации' \
--data-urlencode 'scope=GIGACHAT_API_PERS'
```

Где:

* `RqUID` — обязательный заголовок, в котором нужно передать уникальный идентификатор запроса в формате `uuid4`. Идентификатор нужно указать самостоятельно, для этого можно использовать стандартные библиотеки и классы для генерации UUID и GUID.
* `Authorization` — обязательный заголовок, в котором нужно передать [ключ авторизации](/ru/gigachat/quickstart/ind-using-api).
* `scope` — обязательное поле в теле запроса, которое указывает к какой версии API выполняется запрос. Возможные значения:
  * `GIGACHAT_API_PERS` — доступ для физических лиц.
  * `GIGACHAT_API_B2B` — доступ для ИП и юридических лиц по [платным пакетам](/ru/gigachat/quickstart/legal-tokens-purchase).
  * `GIGACHAT_API_CORP` — доступ для ИП и юридических лиц по схеме [pay-as-you-go](/ru/gigachat/quickstart/legal-tokens-purchase).

При успешном выполнении запроса GigaChat API вернет токен доступа, который действует в течение 30 минут:

```json
{
  "access_token": "eyJhbGci3iJkaXIiLCJlbmMiOiJBMTI4R0NNIiwidHlwIjoiSldUIn0..Dx7iF7cCxL8SSTKx.Uu9bPK3tPe_crdhOJqU3fmgJo_Ffvt4UsbTG6Nn0CHghuZgA4mD9qiUiSVC--okoGFkjO77W.vjYrk3T7vGM6SoxytPkDJw",
  "expires_at": 1679471442
}
```

Запросы на получение токена можно отправлять до 10 раз в секунду.

:::note

Как получить ключ авторизации и токен доступа Access token читайте в разделах [Быстрый старт для физических лиц](/ru/gigachat/individuals-quickstart) и [Быстрый старт для ИП и юридических лиц](/ru/gigachat/legal-quickstart).

:::

## Обращение к моделям в раннем доступе

Модели для генерации GigaChat регулярно обновляются и у них появляются новые возможности, например, вызов функций.
В таких случаях новые версии моделей некоторое время доступны в раннем доступе.

Подробнее — в разделе [Модели GigaChat](/ru/gigachat/models/preview-models).

<div style={{"marginBottom":"2rem"}}>
  <Heading id={"authentication"} as={"h2"} className={"openapi-tabs__heading"} children={"Authentication"} />

  <SchemaTabs className={"openapi-tabs__security-schemes"}>
    <TabItem label={"HTTP: Basic Auth"} value={"Базовая аутентификация"}>
      Базовая (Basic) аутентификация с помощью ключа авторизации — строки, полученной в результате кодирования в base64 идентификатора (Client ID) и клиентского ключа (Client Secret) API.

      Ключ авторизации передается в заголовке `Authorization`, в запросе на [получение токена доступа](/ru/gigachat/api/reference/rest/post-token).

      Как получить ключ авторизации и токен доступа Access token читайте в разделах [Быстрый старт для физических лиц](/ru/gigachat/individuals-quickstart) и [Быстрый старт для ИП и юридических лиц](/ru/gigachat/legal-quickstart).

      <div>
        <table>
          <tbody>
            <tr>
              <th>
                Security Scheme Type:
              </th>

              <td>
                http
              </td>
            </tr>

            <tr>
              <th>
                HTTP Authorization Scheme:
              </th>

              <td>
                basic
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </TabItem>

    <TabItem label={"HTTP: Bearer Auth"} value={"Токен доступа"}>
      Аутентификация с помощью токена доступа Access token. Используется во всех запросах к GigaChat API, кроме запроса на [получение токена доступа](/ru/gigachat/api/reference/rest/post-token).

      <div>
        <table>
          <tbody>
            <tr>
              <th>
                Security Scheme Type:
              </th>

              <td>
                http
              </td>
            </tr>

            <tr>
              <th>
                HTTP Authorization Scheme:
              </th>

              <td>
                bearer
              </td>
            </tr>

            <tr>
              <th>
                Bearer format:
              </th>

              <td>
                JWT
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </TabItem>
  </SchemaTabs>
</div>


# Сгенерировать ответ

[source](https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/post-chat.md)

<Heading as={"h1"} className={"openapi__heading"} children={"Сгенерировать ответ"} />

<MethodEndpoint method={"post"} path={"/chat/completions"} context={"endpoint"} />

Возвращает ответ модели, сгенерированный на основе переданных сообщений.

Передавайте текст сообщений (поле `content`) в кодировке UTF8.
Это позволит снизить расход токенов при обработке сообщения.

При генерации ответа модель может учитывать текстовые документы, изображения и аудиофайлы, сохраненные в хранилище.
Для этого передайте список идентификаторов файлов в массиве `attachments`.
При использовании больших текстовых файлов в запросах на генерацию, их содержимое может превышать [размер контекста модели](/ru/gigachat/models).
В таком случае вернется [ошибка с кодом 422](/ru/gigachat/api/errors-description?responseCode=422).

В одном сообщении (объект в массиве `messages`) можно передать только одно изображение.
В одном запросе можно передать до 10 изображений, независимо от количества сообщений.

:::note

При этом общий размер запроса при работе с изображениями и аудио должен быть меньше 80 Мб.

Например, ваш запрос может включать текст промпта и идентификаторы изображения размером 12 Мб, и двух аудиофайлов размером 33 Мб и 21 Мб. Что в сумме даст запрос размером больше 66 Мб, в зависимости от размера промпта.

Размер текстовых документов не влияет на размер запроса, но их содержимое может превышать контекстное окно модели.

:::

Подробнее — в разделе [Обработка файлов](/ru/gigachat/guides/working-with-files).

Запрос на генерацию можно передавать [моделям в раннем доступе](/ru/gigachat/models).
К названию модели, которое передается в поле `model`, добавьте постфикс `-preview`.

<Heading id={"request"} as={"h2"} className={"openapi-tabs__heading"} children={"Request"} />

<ParamsDetails parameters={[{"name":"X-Client-ID","in":"header","description":"Произвольный идентификатор пользователя, который используется для логирования.\n\nЕсли вы передали этот заголовок при запросе на создание изображения, то для скачивания изображения в запросе <APIMethod type=\"GET\" path=\"/files/{file_id}/content\" link=\"/ru/gigachat/api/reference/rest/get-file-id\"/> нужно передать этот же заголовок.\n","schema":{"type":"string"}},{"in":"header","name":"X-Request-ID","description":"Произвольный идентификатор запроса, который используется для логирования.","schema":{"type":"string"}},{"in":"header","name":"X-Session-ID","description":"Произвольный идентификатор сессии, который используется для логирования.","schema":{"type":"string"}}]} />

<RequestSchema title={"Body"} body={{"content":{"application/json":{"schema":{"required":["model","messages"],"type":"object","properties":{"model":{"description":"Название и версия модели, которая сгенерировала ответ. Описание доступных моделей смотрите в разделе [Модели GigaChat](/ru/gigachat/models).\n\nПри обращении к моделям в раннем доступе к названию модели нужно добавлять постфикс `-preview`.\nНапример, `GigaChat-Pro-preview`.\n","type":"string","example":"GigaChat:1.0.26.20"},"messages":{"type":"array","description":"Массив сообщений, которыми пользователь обменивался с моделью.\n\nВ запросе можно передать только один системный промпт (сообщение с ролью `system`).\nСистемный промпт должен быть первым сообщением в массиве.\n\nНаличие в массиве нескольких системных промптов или передача системного промпта не в первом сообщении приведет к ошибке [с кодом 422](/ru/gigachat/api/errors-description?responseCode=422) и сообщением `Invalid params: system message must be the first message`.\n","items":{"type":"object","properties":{"role":{"type":"string","description":"Роль автора сообщения:\n\n* `system` — системный промпт, который задает роль модели, например, должна модель отвечать как академик или как школьник;\n* `assistant` — ответ модели;\n* `user` — сообщение пользователя;\n* `function` — сообщение с результатом работы [пользовательской функции](/ru/gigachat/guides/functions/generating-arguments-for-custom-functions). В сообщении с этой ролью передавайте результаты работы функции в поле `content` в форме валидного JSON-объекта, обернутого в строку.\n\nДля сохранения контекста диалога с пользователем передайте несколько сообщений. Подробнее читайте в разделе [Работа с историей чата](/ru/gigachat/guides/keeping-context).\n","enum":["system","user","assistant","function"],"example":"function"},"content":{"description":"Содержимое сообщения. Зависит от роли.\n\nЕсли поле передается в сообщении с ролью `function`, то в нем указывается обернутый в строку валидный JSON-объект с аргументами функции, указанной в поле `function_call.name`.\n\nВ остальных случаях содержит либо системный промпт (сообщение с ролью `system`), либо текст сообщения пользователя или модели.\n\nПередавайте текст в кодировке UTF8.\nЭто позволит снизить расход токенов при обработке сообщения.\n","type":"string","example":"{\"temperature\": \"27\"}"},"functions_state_id":{"type":"string","format":"uuidv4","description":"Идентификатор, который объединяет массив функций, переданных в запросе.\nВозвращается в ответе модели (сообщение с `\"role\": \"assistant\"`) при вызове встроенных или собственных функций.\nПозволяет сохранить [состояние обращения к функции (собственной или встроенной)](/ru/gigachat/guides/functions/calling-builtin-functions#sohranenie-konteksta) и повысить качество работы модели.\nДля этого нужно передать идентификатор в запросе на генерацию в сообщении с ролью `assistant`.\n\nСейчас поле работает только при обращении к [моделям в раннем доступе](/ru/gigachat/models/preview-models).\n","example":"77d3fb14-457a-46ba-937e-8d856156d003"},"attachments":{"description":"Массив идентификаторов файлов, которые нужно использовать при генерации.\nИдентификатор присваивается файлу при [загрузке в хранилище](/ru/gigachat/api/reference/rest/post-file).\nПосмотреть список файлов в хранилище можно с помощью метода <APIMethod type=\"GET\" path=\"/files\" link=\"/ru/gigachat/api/reference/rest/get-files\"/>.\n\nПри работе с текстовыми документами в одном запросе на генерацию нужно передавать только один идентификатор.\nЕсли вы передадите несколько идентификаторов файлов, для генерации будет использован только первый файл из списка.\nПри использовании больших текстовых файлов в запросах на генерацию, их содержимое может превышать [размер контекста модели](/ru/gigachat/models#modeli-dlya-generatsii).\nВ таком случае вернется [ошибка с кодом 422](/ru/gigachat/api/errors-description?responseCode=422).\n\nВ одном сообщении (объект в массиве `messages`) можно передать только одно изображение.\nВ одной сессии можно передать до 10 изображений.\n\n\n<Admonition type=\"note\">\n\n\nПри этом общий размер запроса при работе с изображениями и аудио должен быть меньше 80 Мб.\n\nНапример, ваш запрос может включать текст промпта и идентификаторы изображения размером 12 Мб, и двух аудиофайлов размером 33 Мб и 21 Мб. Что в сумме даст запрос размером больше 66 Мб, в зависимости от размера промпта.\n\nРазмер текстовых документов не влияет на размер запроса, но их содержимое может превышать контекстное окно модели.\n\n\n</Admonition>\n\n\nПодробнее — в разделе [Обработка файлов](/ru/gigachat/guides/working-with-files)\n","type":"array","items":{"type":"string","example":["e7f0b84b-3d4f-4c2c-ac31-8855b1b0db0a"]}}},"title":"message"}},"function_call":{"description":"Явно задает [режим работы с функциями](/ru/gigachat/guides/functions/function-calling-modes).\nМожет быть строкой или объектом.\n\nВозможные значения:\n\n* `none` — модель не будет вызывать встроенные функции или генерировать аргументы для пользовательских функций, а просто сгенерирует ответ в соответствии с полученными сообщениями;\n\n* `auto` — в авторежиме модель, основываясь на тексте сообщений, решает нужно ли использовать одну из [встроенных функций](/ru/gigachat/guides/functions/calling-builtin-functions) или сгенерировать аргументы для пользовательских функций, описанных в массиве `functions`. При этом, если массив содержит описание хотя бы одной пользовательской функции, модель сможет вызвать встроенную функцию, только если ее название передано в массиве `functions`;\n\n  ```json \n  {\n  \t\"function_call\": \"auto\",\n    \"functions\": [\n  \t  {\n          \"name\": \"text2image\"\t\t\t\n  \t  },\n      {\n          \"name\": \"weather_forecast\",\n          \"description\": \"Возвращает температуру на заданный период\",\n          \"parameters\": {}\n      }\n    ]\n  }\n  ```\n\n* `{\"name\": \"название_функции\"}` — принудительная генерация аргументов для указанной функции. При принудительной генерации аргументов для пользовательской функции ее описание нужно обязательно передавать в массиве `functions`.\nВ противном случае вернется ошибка.\n","oneOf":[{"type":"object","properties":{"name":{"type":"string","description":"Название функции.\n\nВ поле можно передать как название собственной функции, описание которой содержится в массиве `functions`, так и название одной из [встроенных функций](/ru/gigachat/guides/functions/calling-builtin-functions).\n","example":"weather_forecast"}},"title":"function_call_name"},{"type":"string","enum":["auto","none"],"description":"Режим работы с функциями:\n\n* `auto` — в авторежиме модель, основываясь на тексте сообщений, решает нужно ли использовать одну из [встроенных функций](/ru/gigachat/guides/functions/calling-builtin-functions) или сгенерировать аргументы для пользовательских функций, описанных в массиве `functions`. При этом, если массив содержит описание хотя бы одной пользовательской функции, модель сможет вызвать встроенную функцию, только если ее название передано в массиве `functions`;\n\n* `none` — модель не будет вызывать встроенные функции или генерировать аргументы для пользовательских функций, а просто сгенерирует ответ в соответствии с полученными сообщениями.\n","example":"auto","title":"function_call_none_auto"}]},"functions":{"type":"array","nullable":true,"description":"Массив с описанием пользовательских функций.","items":{"description":"Описание пользовательской функции.","type":"object","required":["name","parameters"],"properties":{"name":{"type":"string","description":"Название пользовательской функции, для которой будут сгенерированы аргументы.\n\n:::caution\n\nНазвание функции должно содержать только латинские буквы.\nНазвание функции не должно начинаться с цифры.\n\n:::\n","example":"pizza_order"},"description":{"type":"string","description":"Текстовое описание функции.","example":"Функция для заказа пиццы"},"parameters":{"type":"object","properties":{},"description":"Валидный JSON-объект с набором пар `ключ-значение`, которые описывают аргументы функции."},"few_shot_examples":{"type":"array","description":"Объекты с парами `запрос_пользователя`-`параметры_функции`, которые будут служить модели примерами ожидаемого результата.\n","items":{"type":"object","required":["request","params"],"properties":{"request":{"type":"string","description":"Запрос пользователя.","example":"Погода в Москве в ближайшие три дня"},"params":{"type":"object","description":"Пример заполнения параметров пользовательской функции.","properties":{}}}}},"return_parameters":{"type":"object","description":"JSON-объект с описанием параметров, которые может вернуть ваша функция.","properties":{}}},"title":"CustomFunction"},"title":"CustomFunctions"},"temperature":{"format":"float","type":"number","description":"Температура выборки. Чем выше значение, тем более случайным будет ответ модели. Если значение температуры находится в диапазоне от 0 до 0.001, параметры `temperature` и `top_p` будут сброшены в режим, обеспечивающий максимально детерминированный (стабильный) ответ модели. При значениях температуры больше двух, набор токенов в ответе модели может отличаться избыточной случайностью.\n\nЗначение по умолчанию зависит от выбранной модели (поле `model`) и может изменяться с обновлениями модели.\n","minimum":0,"exclusiveMinimum":true,"nullable":true},"top_p":{"format":"float","type":"number","description":"Параметр используется как альтернатива температуре (поле `temperature`). Задает вероятностную массу токенов, которые должна учитывать модель.\nТак, если передать значение 0.1, модель будет учитывать только токены, чья вероятностная масса входит в верхние 10%.\n\nЗначение по умолчанию зависит от выбранной модели (поле `model`) и может изменяться с обновлениями модели.\n\nЗначение изменяется в диапазоне от 0 до 1 включительно.\n","minimum":0,"maximum":1,"nullable":true},"stream":{"type":"boolean","description":"Указывает что сообщения надо передавать по частям в потоке.\n\nСообщения передаются по протоколу [SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events#event_stream_format).\n\nПоток завершается событием `data: [DONE]`.\n\nПодробнее читайте в разделе [Потоковая генерация токенов](/ru/gigachat/guides/response-token-streaming).\n","default":false,"example":false},"max_tokens":{"description":"Максимальное количество токенов, которые будут использованы для создания ответов.","format":"int32","type":"integer","nullable":true},"repetition_penalty":{"type":"number","format":"float","nullable":true,"description":"Количество повторений слов:\n\n* Значение 1.0 — нейтральное значение.\n* При значении больше 1 модель будет стараться не повторять слова.\n\nЗначение по умолчанию зависит от выбранной модели (поле `model`) и может изменяться с обновлениями модели.\n","example":1},"update_interval":{"type":"number","description":"Параметр потокового режима (`\"stream\": \"true\"`).\nЗадает минимальный интервал в секундах, который проходит между отправкой токенов.\nНапример, если указать `1`, сообщения будут приходить каждую секунду, но размер каждого из них будет больше, так как за секунду накапливается много токенов.\n","default":0,"example":0}},"title":"Chat"},"examples":{"Текст":{"value":{"model":"GigaChat","messages":[{"role":"system","content":"Ты — профессиональный переводчик на английский язык. Переведи точно сообщение пользователя."},{"role":"user","content":"GigaChat — это сервис, который умеет взаимодействовать с пользователем в формате диалога, писать код, создавать тексты и картинки по запросу пользователя."}],"stream":false,"update_interval":0}},"Изображение":{"value":{"model":"GigaChat","messages":[{"role":"system","content":"Ты — Василий Кандинский"},{"role":"user","content":"Нарисуй розового кота"}],"function_call":"auto"}},"Аргументы для функции":{"value":{"model":"GigaChat-2-Pro","messages":[{"role":"user","content":"Погода в Манжероке на десять дней"}],"functions":[{"name":"weather_forecast","description":"Возвращает температуру на заданный период","parameters":{"type":"object","properties":{"location":{"type":"string","description":"Местоположение, например, название города"},"format":{"type":"string","enum":["celsius","fahrenheit"],"description":"Единицы измерения температуры"},"num_days":{"type":"integer","description":"Период, для которого нужно вернуть"}},"required":["location","num_days"]},"few_shot_examples":[{"request":"Какая погода в Москве в ближайшие три дня","params":{"location":"Moscow, Russia","format":"celsius","num_days":"3"}}],"return_parameters":{"type":"object","properties":{"location":{"type":"string","description":"Местоположение, например, название города"},"temperature":{"type":"integer","description":"Температура для заданного местоположения"},"forecast":{"type":"array","items":{"type":"string"},"description":"Описание погодных условий"},"error":{"type":"string","description":"Возвращается при возникновении ошибки. Содержит описание ошибки"}}}}]}}}}}}} />

<StatusCodes id={undefined} label={undefined} responses={{"200":{"content":{"application/json":{"schema":{"type":"object","properties":{"choices":{"type":"array","description":"Массив ответов модели.","items":{"type":"object","properties":{"message":{"type":"object","description":"Сгенерированное сообщение.","properties":{"role":{"type":"string","enum":["assistant","function_in_progress"],"description":"Роль автора сообщения.\n\nРоль `function_in_progress` используется при работе встроенных функций в режиме [потоковой передачи токенов](/ru/gigachat/guides/functions/calling-builtin-functions#potokovaya-peredacha-tokenov).\n","example":"assistant"},"content":{"type":"string","description":"Содержимое сообщения, например, результат генерации.\nПри передаче в [режиме потоковой генерации](/ru/gigachat/guides/response-token-streaming) передается частями. В предпосленем сообщении передаеся пустая строка `\"\"`.\n\nВ сообщениях с ролью `function_in_progress` содержит информацию о том, сколько времени осталось до завершения работы встроенной функции.\n","example":"Здравствуйте! К сожалению, я не могу дать точный ответ на этот вопрос, так как это зависит от многих факторов. Однако обычно релиз новых функций и обновлений в GigaChat происходит постепенно и незаметно для пользователей. Рекомендую следить за новостями и обновлениями проекта в официальном сообществе GigaChat или на сайте разработчиков."},"created":{"type":"integer","format":"unix timestamp","description":"Передается в сообщениях с ролью`function_in_progress`. Содержит информацию о том, когда был создан фрагмент сообщения.","example":1625284800},"name":{"type":"string","description":"Название вызванной [встроенной функции](/ru/gigachat/guides/functions/calling-builtin-functions).\nПередается в сообщениях с ролью`function_in_progress`.\nВозможные значения:\n\n* `text2image` - генерация изображения на основе описания;\n* `text2model3d` — генерация 3D-модели на основе описания.\n","example":"text2image"},"functions_state_id":{"type":"string","format":"uuidv4","description":"Идентификатор, который объединяет массив функций, переданных в запросе.\nВозвращается в ответе модели (сообщение с `\"role\": \"assistant\"`) при вызове встроенных или собственных функций.\nПозволяет сохранить [контекст вызова функции](/ru/gigachat/guides/functions/calling-builtin-functions#sohranenie-konteksta) и повысить качество работы модели.\nДля этого нужно передать идентификатор в запросе на генерацию в сообщении с ролью `assistant`.\n\nСейчас поле работает только при обращении к [моделям в раннем доступе](/ru/gigachat/models/preview-models).\n","example":"77d3fb14-457a-46ba-937e-8d856156d003"},"function_call":{"type":"object","properties":{"name":{"type":"string","description":"Название функции."},"arguments":{"type":"object","description":"Аргументы для вызова функции в виде пар ключ-значение."}}}},"title":"MessagesRes"},"index":{"format":"int32","type":"integer","description":"Индекс сообщения в массиве, начиная с ноля.","example":0},"finish_reason":{"description":"Причина завершения гипотезы. Возможные значения:\n\n* `stop` — модель закончила формировать гипотезу и вернула полный ответ;\n* `length` — достигнут лимит токенов в сообщении;\n* `function_call` — указывает, что при запросе была вызвана встроенная функция или сгенерированы аргументы для пользовательской функции;\n* `blacklist` — запрос попадает под [тематические ограничения](/ru/gigachat/limitations#tematicheskie-ogranicheniya-zaprosov).\n* `error` — ответ модели содержит невалидные аргументы пользовательской функции.\n\nПри работе в режиме [потоковой генерации](/ru/gigachat/guides/response-token-streaming) передается в предпоследнем событии со значением.\n","type":"string","enum":["stop","length","function_call","blacklist","error"],"example":"stop"}},"title":"Choices"}},"created":{"format":"unix timestamp","type":"integer","description":"Дата и время создания ответа в формате unix timestamp.","example":1678878333},"model":{"description":"Название и версия модели, которая сгенерировала ответ. Описание доступных моделей смотрите в разделе [Модели GigaChat](/ru/gigachat/models).\n\nПри обращении к моделям в раннем доступе к названию модели нужно добавлять постфикс `-preview`.\nНапример, `GigaChat-Pro-preview`.\n","type":"string","example":"GigaChat:1.0.26.20"},"usage":{"type":"object","description":"Данные об использовании модели.\nПри запуске [потоковой генерации](/ru/gigachat/guides/response-token-streaming), объект приходит в предпоследнем событии.\n","properties":{"prompt_tokens":{"format":"int32","description":"Количество токенов во входящем сообщении (роль `user`).","type":"integer","example":1},"completion_tokens":{"format":"int32","description":"Количество токенов, сгенерированных моделью (роль `assistant`).","type":"integer","example":4},"precached_prompt_tokens":{"format":"int32","description":"Количество ранее закэшированных токенов, которые были использованы при обработке запроса.\nКэшированные токены вычитаются из общего числа оплачиваемых токенов (поле `total_tokens`).\n\nМодели GigaChat в течение некоторого времени сохраняют контекст запроса (историю сообщений массива `messages`, описание функций) с помощью кэширования токенов. Это позволяет повысить скорость ответа моделей и снизить стоимость работы с GigaChat API.\n\n\n<Admonition type=\"tip\">\n\n\nДля повышения вероятности использования сохраненных токенов используйте [кэширование запросов](/ru/gigachat/guides/keeping-context#keshirovanie-zaprosov).\n\n\n</Admonition>\n\n\n[Подробнее о подсчете токенов](/ru/gigachat/guides/counting-tokens).\n","type":"integer","example":37},"total_tokens":{"format":"int32","description":"Общее число токенов, подлежащих тарификации, после вычитания кэшированных токенов (поле `precached_prompt_tokens`).","type":"integer","example":5}},"title":"Usage"},"object":{"type":"string","description":"Название вызываемого метода.","example":"chat.completion"}},"title":"ChatCompletion"},"examples":{"Текст":{"value":{"choices":[{"message":{"content":"GigaChat is a service capable of interacting with the user in a dialogue format, writing code, and creating texts and images upon user's request.","role":"assistant"},"index":0,"finish_reason":"stop"}],"created":1760434636,"model":"GigaChat:2.0.28.2","object":"chat.completion","usage":{"prompt_tokens":55,"completion_tokens":30,"total_tokens":85,"precached_prompt_tokens":4}}},"Изображение":{"value":{"choices":[{"message":{"content":"<img src=\"3727db23-91a3-44fa-a6b7-9f0a311d3e9e\" fuse=\"true\"/> вот мой рисунок розового кота.","role":"assistant","functions_state_id":"0199e20f-058c-70c0-9850-6400dd41a853"},"index":0,"finish_reason":"stop"}],"created":1760434259,"model":"GigaChat:2.0.28.2","object":"chat.completion","usage":{"prompt_tokens":626,"completion_tokens":43,"total_tokens":669,"precached_prompt_tokens":3}}},"Аргументы для функции":{"value":{"choices":[{"message":{"content":"","role":"assistant","function_call":{"name":"weather_forecast","arguments":{"location":"Манжерок","num_days":10}},"functions_state_id":"0199e210-2f13-744c-8fe5-c9a19fe27db7"},"index":0,"finish_reason":"function_call"}],"created":1760434335,"model":"GigaChat-2-Pro:2.0.28.2","object":"chat.completion","usage":{"prompt_tokens":278,"completion_tokens":35,"total_tokens":313,"precached_prompt_tokens":0}}}}},"text/event-stream":{"schema":{"type":"string","description":"Событие формата [Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html).\nКаждое событие содерджит сообщение вида `data: <JSON-объект с фрагментом ответа модели>`.\nВ последнем событии приходит сообщение `data: [DONE]`.\n\nПример фрагмента:\n\n```json\n{\n    \"choices\": [\n        {\n            \"delta\": {\n                \"content\": \"GigaChat is a service capable of interacting with the user in a dialogue format, writing code, and creating texts and images upon the user's request.\",\n                \"role\": \"assistant\"\n            },\n            \"index\": 0\n        }\n    ],\n    \"created\": 1754637655,\n    \"model\": \"GigaChat:2.0.28.2\",\n    \"object\": \"chat.completion\"\n}\n```\n"},"example":"data: {\"choices\":[{\"delta\":{\"content\":\"GigaChat is a service capable of interacting with the user in a dialogue format, writing code, and creating texts and images upon the user's request.\",\"role\":\"assistant\"},\"index\":0}],\"created\":1754637655,\"model\":\"GigaChat:2.0.28.2\",\"object\":\"chat.completion\"}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"\"},\"index\":0,\"finish_reason\":\"stop\"}],\"created\":1754637655,\"model\":\"GigaChat:2.0.28.2\",\"object\":\"chat.completion\",\"usage\":{\"prompt_tokens\":56,\"completion_tokens\":31,\"total_tokens\":87,\"precached_prompt_tokens\":3}}\n\ndata: [DONE]\n"}},"description":"Успешное выполнение запроса.\nПри запуске [потоковой генерации](/ru/gigachat/guides/response-token-streaming) передается с заголовком `Content-Type:\ttext/event-stream`.\n"},"400":{"description":"400 Bad request.\n\nНекорректный формат запроса.\n"},"401":{"description":"Ошибка авторизации.","content":{"application/json":{"schema":{"type":"object","properties":{"status":{"type":"integer","description":"HTTP-код сообщения.","default":401},"message":{"type":"string","description":"Описание ошибки.","default":"Unauthorized"}}}}}},"404":{"description":"Указан неверный идентификатор модели.\n\nСписок доступных моделей и их идентификаторов — в разделе [Модели GigaChat](/ru/gigachat/models).\n","content":{"application/json":{"schema":{"type":"object","properties":{"status":{"type":"integer","description":"HTTP-код сообщения.","default":404},"message":{"type":"string","description":"Описание ошибки.","default":"No such model"}}}}}},"422":{"description":"Ошибка валидации параметров запроса. Проверьте названия полей и значения параметров.","content":{"application/json":{"schema":{"type":"object","properties":{"status":{"type":"integer","description":"HTTP-код сообщения.","default":422},"message":{"type":"string","description":"Описание ошибки.","example":"Invalid params: repetition_penalty must be in range (0, +inf)"}}}}}},"429":{"description":"Слишком много запросов в единицу времени.","content":{"application/json":{"schema":{"type":"object","properties":{"status":{"type":"integer","description":"HTTP-код сообщения.","default":429},"message":{"type":"string","description":"Описание ошибки.","default":"Too many requests"}}}}}},"500":{"description":"Внутренняя ошибка сервера.","content":{"application/json":{"schema":{"type":"object","properties":{"status":{"type":"integer","description":"HTTP-код сообщения.","default":500},"message":{"type":"string","description":"Описание ошибки.","default":"Internal Server Error"}}}}}}}} />