# Data Models

| Issue | Status |
| ----- | ------ |
| #88   | Draft  |

There are two data models required for Cart API: `Cart` and `CartItem`. They are
designed in the way that allows multiple types of entity in a single `Cart`.

## `Cart`
| Property | Type | Description |
| --- | --- | --- |
| `CartId` | UUID | Cart ID, auto-generated by the web API | - |
| `UserId` | UUID | The ID of the user that creates the collection<sup>1</sup> |
| `CartName` | `str` | Cart Name |
| `DefaultCart` | `bool` | Flag to indicate whether this cart is the default one for a corresponding user<sup>2</sup> |
| `CollectionId` | UUID | DSS Collection ID (added 2018-12-17) |

1. Use the subject of the JWT according to [the authentication flow RFC](https://allspark.dev.data.humancellatlas.org/dcp-ops/docs/wikis/RFC:-Authentication-Flow#given-a-decoded-token-what-can-we-use-as-user-identifier).
2. `False` by default

For the details on the default cart, see `GET /resources/carts/{id}` in the proposal for the API spec.

## `CartItem`
| Property | Type | Description |
| --- | --- | --- |
| `CartItemId` | `str` | Cart Item ID* |
| `CartId` | UUID | Associated `Cart.id` |
| `EntityId` | UUID | Entity ID |
| `EntityType` | `str` | Entity Type |
| `BundleUuid` | UUID | Bundle ID |
| `BundleVersion` | `str` | Bundle Version |

> Cart Item ID is a hash string of the combination of `CartId`, `EntityId`, `EntityType`, `BundleUuid`, `BundleVersion`. The hash algorithm is probably `sha256` but it is not finalized.
