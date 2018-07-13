#!/usr/bin/env bash

url="http://$AZUL_ES_ENDPOINT"

# Populate the File Oriented ElasticSearch index
echo "Updating fb_index"
curl -XDELETE $url/fb_buffer/
curl -XPUT $url/fb_buffer/ -d @test/fb_settings.json
curl -XPUT $url/fb_buffer/_mapping/meta?update_all_types  -d @test/fb_mapping.json
curl -XPUT $url/fb_buffer/_bulk?pretty --data-binary @test/fb_index.jsonl

# Change alias to point to fb_buffer
curl -XPOST $url/_aliases?pretty -d' { "actions" : [ { "remove" : { "index" : "fb_real", "alias" : "fb_index" } }, { "add" : { "index" : "fb_buffer", "alias" : "fb_index" } } ] }'

# Index/Update the data in fb_index
curl -XDELETE $url/fb_real/
curl -XPUT $url/fb_real/ -d @test/fb_settings.json
curl -XPUT $url/fb_real/_mapping/meta?update_all_types  -d @test/fb_mapping.json
curl -XPUT $url/fb_real/_bulk?pretty --data-binary @test/fb_index.jsonl

#Change alias one last time
curl -XPOST $url/_aliases?pretty -d' { "actions" : [ { "remove" : { "index" : "fb_buffer", "alias" : "fb_index" } }, { "add" : { "index" : "fb_real", "alias" : "fb_index" } } ] }'

# Populate the Donor Oriented index
echo "Updating analysis_index"
curl -XDELETE $url/analysis_buffer/
curl -XPUT $url/analysis_buffer/ -d @test/donor_settings.json
curl -XPUT $url/analysis_buffer/_mapping/meta?update_all_types  -d @test/donor_mapping.json
curl -XPUT $url/analysis_buffer/_bulk?pretty --data-binary @test/donor_index.jsonl

# Change alias to point to analysis_buffer
curl -XPOST $url/_aliases?pretty -d' { "actions" : [ { "remove" : { "index" : "analysis_real", "alias" : "fb_index" } }, { "add" : { "index" : "analysis_buffer", "alias" : "analysis_index" } } ] }'

# Index/Update the data in analysis_index
curl -XDELETE $url/analysis_real/
curl -XPUT $url/analysis_real/ -d @test/donor_settings.json
curl -XPUT $url/analysis_real/_mapping/meta?update_all_types  -d @test/donor_mapping.json
curl -XPUT $url/analysis_real/_bulk?pretty --data-binary @test/donor_index.jsonl

#Change alias one last time
curl -XPOST $url/_aliases?pretty -d' { "actions" : [ { "remove" : { "index" : "analysis_buffer", "alias" : "analysis_index" } }, { "add" : { "index" : "analysis_real", "alias" : "analysis_index" } } ] }'
