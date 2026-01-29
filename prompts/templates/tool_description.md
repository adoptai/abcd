# Tool Action Guidelines

This is a tool action that we are building.

## Requirements

1. A tool action must have exactly one REST operation.

2. It should be followed by an optional JQ FILTER, extract, agent operations based what the user is trying to extract from the output of the REST operation.

3. It should be followed by an OUTPUT_TEXT block with raw set to true with outputs from the JQ filter or REST operation. The canonical api end point and url for this rest block should match the path parameter in the api details. If the api is graphql based then make sure the query field is populated correctly accordingly to the examples shown in api details.

4. It should have a required_inputs filed in exactly the format given in below examples.

5. No other operations or fields are allowed other than REST, JQ_FILTER, EXTRACT, PROJECT, OUTPUT_TEXT, and required_inputs

6. When an input is optional for calling an API, set a default value for it as shown below in price_owner_code and mark it as optional. All other required inputs must be marked mandatory

## Example 1: calls an offers API with all required inputs and sends the API output raw.

```json
[
  {
    "additional_headers": {
      "Consumer-Key": "{security_parameters.consumer_key}"
    },
    "canonical_api_endpoint": "/api/v1/offers",
    "id": "get_product_offers",
    "method": "POST",
    "operation": "REST",
    "payload": {
      "brandCode": "{workflow_arguments.brand_code}",
      "commodity": {
        "dangerousDetails": [],
        "id": "{workflow_arguments.commodity_id}",
        "isDangerous": "{workflow_arguments.commodity_isDangerous}"
      },
      "containers": [
        {
          "isNonOperatingReefer": "{workflow_arguments.container_isNonOperatingReefer}",
          "isReefer": "{workflow_arguments.container_isReefer}",
          "isShipperOwnedContainer": "{workflow_arguments.container_isShipperOwned}",
          "isoCode": "{workflow_arguments.container_details_isoCode}",
          "quantity": "{workflow_arguments.container_details_quantity}",
          "size": "{workflow_arguments.container_details_size}",
          "type": "{workflow_arguments.container_details_type}",
          "weight": "{workflow_arguments.container_details_weight}"
        }
      ],
      "from": {
        "countryCode": "{workflow_arguments.origin_location_countryCode}",
        "locationId": "{workflow_arguments.origin_location_id}",
        "locationCode": "{workflow_arguments.origin_location_code}",
        "serviceMode": "{workflow_arguments.origin_service_mode}"
      },
      "loadAdditionalServices": false,
      "priceOwnerCode": "{workflow_arguments.price_owner_code}",
      "shipmentPriceCalculationDate": "{workflow_arguments.shipment_date}",
      "to": {
        "countryCode": "{workflow_arguments.destination_location_countryCode}",
        "locationId": "{workflow_arguments.destination_location_id}",
        "locationCode": "{workflow_arguments.destination_location_code}",
        "serviceMode": "{workflow_arguments.destination_service_mode}"
      },
      "unit": "{workflow_arguments.weight_unit}",
      "weekOffset": 0
    },
    "url": "/api/v1/offers"
  },
  {
    "format_string": "Product Offers: {}",
    "id": "display_product_offers",
    "operation": "OUTPUT_TEXT",
    "raw": true,
    "values": [
      "get_product_offers"
    ]
  },
  {
    "required_inputs": [
      "{\"container_details_isoCode\" : {\"type\": \"string\", \"definition\": \"ISO code for the container type (mandatory)\"}}",
      "{\"container_details_quantity\" : {\"type\": \"number\", \"definition\": \"Quantity of the container (mandatory)\"}}",
      "{\"container_details_size\" : {\"type\": \"number\", \"definition\": \"Size of the container (mandatory)\"}}",
      "{\"container_details_type\" : {\"type\": \"string\", \"definition\": \"Type of container (mandatory)\"}}",
      "{\"container_details_weight\" : {\"type\": \"number\", \"definition\": \"Weight of the container (mandatory)\"}}",
      "{\"origin_location_countryCode\" : {\"type\": \"string\", \"definition\": \"Origin country code (mandatory)\"}}",
      "{\"origin_location_id\" : {\"type\": \"string\", \"definition\": \"Origin location identifier (mandatory)\"}}",
      "{\"origin_location_code\" : {\"type\": \"string\", \"definition\": \"Origin location code (mandatory)\"}}",
      "{\"origin_service_mode\" : {\"type\": \"string\", \"definition\": \"Service mode at origin (e.g., CY, Door) (mandatory)\"}}",
      "{\"destination_location_countryCode\" : {\"type\": \"string\", \"definition\": \"Destination country code (mandatory)\"}}",
      "{\"destination_location_id\" : {\"type\": \"string\", \"definition\": \"Destination location identifier (mandatory)\"}}",
      "{\"destination_location_code\" : {\"type\": \"string\", \"definition\": \"Destination location code (mandatory)\"}}",
      "{\"destination_service_mode\" : {\"type\": \"string\", \"definition\": \"Service mode at destination (mandatory)\"}}",
      "{\"price_owner_code\" : {\"default\": \"abc123\", \"type\": \"string\", \"definition\": \"Price owner code (optional)\"}}",
      "{\"commodity_id\" : {\"type\": \"string\", \"definition\": \"Commodity identifier (mandatory)\"}}",
      "{\"commodity_isDangerous\" : {\"type\": \"boolean\", \"definition\": \"Is the commodity dangerous? (mandatory)\"}}",
      "{\"container_isNonOperatingReefer\" : {\"type\": \"boolean\", \"definition\": \"Is it a non-operating reefer? (mandatory)\"}}",
      "{\"container_isReefer\" : {\"type\": \"boolean\", \"definition\": \"Is it a reefer container? (mandatory)\"}}",
      "{\"container_isShipperOwned\" : {\"type\": \"boolean\", \"definition\": \"Is it a shipper-owned container? (mandatory)\"}}",
      "{\"brand_code\" : {\"type\": \"string\", \"definition\": \"Brand or carrier code (mandatory)\"}}",
      "{\"weight_unit\" : {\"type\": \"string\", \"definition\": \"Unit for weight (e.g., KG, LB) (mandatory)\"}}",
      "{\"shipment_date\" : {\"type\": \"string\", \"definition\": \"Shipment date in YYYY-MM-DD format (mandatory)\"}}"
    ]
  }
]
```

## Example 2: get the cost details for assets from an API

```json
[
  {
    "canonical_api_endpoint": "/api/v1/assets",
    "id": "getAssetDetails",
    "method": "GET",
    "operation": "REST",
    "url": "/api/v1/assets"
  },
  {
    "field": "response.data",
    "id": "extractAssetData",
    "input": "getAssetDetails",
    "operation": "EXTRACT"
  },
  {
    "filter": "map(select(.asset_id == \"{workflow_arguments.asset_id}\")) | .[0]",
    "id": "filterAssetById",
    "input": "extractAssetData",
    "operation": "JQ_FILTER"
  },
  {
    "fields": [
      "asset_name",
      "min_cost",
      "sync_toggle",
      "max_cost",
      "lohc_metrics.asset_level.bp_ratio_data.current_bp",
      "currency",
      "city_name",
      "lohc_metrics.asset_level.bp_ratio_data.recommended_bp"
    ],
    "id": "agentCostFields",
    "input": "filterAssetById",
    "operation": "PROJECT"
  },
  {
    "format_string": "{}",
    "id": "displayAssetDetails",
    "operation": "OUTPUT_TEXT",
    "raw": true,
    "values": [
      "agentCostFields"
    ]
  },
  {
    "required_inputs": [
      "{\"asset_id\": { \"type\": \"string\", \"definition\": \"ID of the asset\"}}"
    ]
  }
]
```

