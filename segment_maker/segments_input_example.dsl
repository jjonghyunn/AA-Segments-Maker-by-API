--- segment
name: [CAMPAIGN NAME] CC_02. Main KV (Visit)
rsid: sscompany_name4mstglobal

visit(
  'page+content'!hit(
    '[Global] Campaign Main Page_Evar'!hit(
      @YOUR_ID
    )
    AND
    (
      '[CAMPAIGN NAME] CC_02. Main KV'!hit(
        hit(
          'cc04 component'!hit( customlink starts-with 'cc04_offers kv' )
          AND
          'v26'!hit( event26 event-exists AND evar26 contains 'kv' )
        )
      )
    )
  )
)

===

--- segment
name: [CAMPAIGN NAME] CC_02. Main KV
rsid: sscompany_name4mstglobal

hit(
  '[CAMPAIGN NAME] CC_02. Main KV'!hit(
    hit(
      'cc04 component'!hit( customlink starts-with 'cc04_offers kv' )
      AND
      'v26'!hit( event26 event-exists AND evar26 contains 'kv' )
    )
  )
)
